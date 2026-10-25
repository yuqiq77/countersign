"""Temporal Activities of profile-driven discovery (ADR-017, ticket 005).

The brief, the search, the queues and the screen. Model-level failures are
outcomes, never exceptions, so a failed search never stops the boards; an
exception here is an infrastructure failure for Temporal to retry. The
profile is loaded here, never carried through workflow history.
"""

from datetime import timedelta

from temporalio import activity

from countersign.activities.watch_activities import candidate_key, case_id_for
from countersign.contracts.enums import CandidateSource, CandidateStatus
from countersign.contracts.workflow_io import (
    BriefInput,
    BriefOutcome,
    DiscoveryQueues,
    QueuedCandidate,
    QueueInput,
    ScreenInput,
    ScreenOutcome,
    SearchInput,
    SearchOutcome,
)
from countersign.domain.discovery import VerdictInputs, listing_hash, verdict_is_stale
from countersign.gateway.discovery import (
    BRIEF_PROMPT_VERSION,
    SCREEN_PROMPT_VERSION,
    ListingFacts,
    discovery_route,
    resolve_discovery_provider,
    run_search,
    screen_posting,
)
from countersign.gateway.discovery import derive_brief as derive_search_brief
from countersign.gateway.evaluator import profile_fingerprint
from countersign.gateway.provider import SearchResponse
from countersign.persistence import discovery, repo
from countersign.persistence.db import session_scope
from countersign.persistence.models import DiscoveryCandidateRow
from countersign.policy.operator_profile import load_operator_profile


def _attempt() -> int:
    try:
        return activity.info().attempt
    except RuntimeError:  # outside an activity context (unit tests)
        return 1


@activity.defn(name="derive_brief")
def derive_brief(payload: BriefInput) -> BriefOutcome:
    """The search brief for the run's profile, derived and stored once per fingerprint.

    It always returns the stored brief, so the search uses the brief the
    record and the UI show: a retry whose earlier attempt stored one returns
    it with the calls that attempt made, without calling the model again.
    """

    profile = load_operator_profile()
    if profile is None or profile_fingerprint(profile) != payload.profile_fingerprint:
        return BriefOutcome(brief=None, failure="the profile changed or is missing", model_calls=0)
    with session_scope() as session:
        stored = discovery.stored_brief(session, payload.profile_fingerprint, BRIEF_PROMPT_VERSION)
    if stored is not None:
        brief, calls = stored
        return BriefOutcome(brief=brief, failure=None, model_calls=calls)
    result = derive_search_brief(resolve_discovery_provider(), profile)
    if result.brief is None:
        return BriefOutcome(brief=None, failure=result.failure, model_calls=result.model_calls)
    with session_scope() as session:
        brief = discovery.save_brief(
            session,
            profile_fingerprint=payload.profile_fingerprint,
            prompt_version=BRIEF_PROMPT_VERSION,
            route=discovery_route(),
            brief=result.brief,
            usage=result.usage.model_dump(mode="json") if result.usage else None,
        )
    return BriefOutcome(brief=brief, failure=None, model_calls=result.model_calls)


@activity.defn(name="search_postings")
def search_postings(payload: SearchInput) -> SearchOutcome:
    """The search step: record every request's queries, then the candidates and leads.

    A candidate some case already covers is not recorded; one already
    recorded is seen again.
    """

    attempt = _attempt()

    def store(index: int, response: SearchResponse) -> None:
        # Its own transaction: these searches left the system whatever follows.
        with session_scope() as session:
            discovery.record_search_request(
                session,
                run_key=payload.run_key,
                attempt=attempt,
                index=index,
                queries=response.queries,
                searches=response.searches,
                usage={
                    "model_id": response.model_id,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cache_read_input_tokens": response.cache_read_input_tokens,
                    "cache_creation_input_tokens": response.cache_creation_input_tokens,
                    "provider_request_id": response.request_id,
                },
            )

    run = run_search(
        resolve_discovery_provider(),
        brief=payload.brief,
        regions=payload.regions,
        max_searches=payload.max_searches,
        max_requests=payload.max_requests,
        on_response=store,
    )
    new = 0
    with session_scope() as session:
        known = {candidate_key(row.url) for row in repo.list_cases(session)}
        for found, status in [
            *((f, CandidateStatus.UNSCREENED) for f in run.accepted),
            *((f, CandidateStatus.LEAD) for f in run.leads),
        ]:
            key = candidate_key(found.candidate.url)
            if key in known:
                continue
            _, created = discovery.record_sighting(
                session,
                posting_key=key,
                url=found.candidate.url,
                source=CandidateSource.SEARCH,
                origin=found.query,
                title=found.candidate.title,
                company=found.candidate.company or None,
                locations=[found.candidate.location] if found.candidate.location else [],
                seen_at=payload.started_at,
                status=status,
                note=found.candidate.reason or None,
            )
            new += created
    return SearchOutcome(
        found=len(run.accepted),
        new=new,
        leads=len(run.leads),
        rejected=run.rejected,
        searches=run.searches,
        model_calls=run.model_calls,
        failure=run.failure,
    )


def _current(row: DiscoveryCandidateRow, fingerprint: str, route: str) -> VerdictInputs:
    """What a verdict on this row would be made from now."""

    board = row.source == CandidateSource.BOARD.value
    return VerdictInputs(
        fingerprint,
        SCREEN_PROMPT_VERSION,
        route,
        listing_hash(
            row.title, repo.ensure_utc(row.published_at) if board and row.published_at else None
        ),
    )


@activity.defn(name="discovery_queues")
def discovery_queues(payload: QueueInput) -> DiscoveryQueues:
    """Each source's candidates to screen and to admit, in its own order (ADR-017).

    Board postings count only when this run listed them: newest first by
    publication time. Search candidates are screened in the order first
    seen and admitted in the order they first passed. A stale verdict is
    screened again before it counts; a stale search drop only while it was
    seen within the lookback period. A posting a case covers is left out.
    """

    route = discovery_route()
    horizon = payload.started_at - timedelta(days=payload.published_within_days)
    with session_scope() as session:
        known = {candidate_key(row.url) for row in repo.list_cases(session)}
        rows = [
            r for r in discovery.open_candidates(session, horizon) if r.posting_key not in known
        ]
    board_screen: list[DiscoveryCandidateRow] = []
    search_screen: list[DiscoveryCandidateRow] = []
    board_passed: list[DiscoveryCandidateRow] = []
    search_passed: list[DiscoveryCandidateRow] = []
    for row in rows:
        board = row.source == CandidateSource.BOARD.value
        if board and repo.ensure_utc(row.last_seen_at) < payload.started_at:
            continue
        stale = verdict_is_stale(
            discovery.verdict_inputs(row), _current(row, payload.profile_fingerprint, route)
        )
        status = CandidateStatus(row.status)
        if status == CandidateStatus.UNSCREENED or (
            stale
            and (
                board
                or status == CandidateStatus.PASS
                or repo.ensure_utc(row.last_seen_at) >= horizon
            )
        ):
            (board_screen if board else search_screen).append(row)
        elif status == CandidateStatus.PASS and not stale:
            (board_passed if board else search_passed).append(row)

    def newest_first(row: DiscoveryCandidateRow) -> tuple[float, int]:
        return -repo.ensure_utc(row.published_at or row.first_seen_at).timestamp(), row.seq

    def queued(row: DiscoveryCandidateRow) -> QueuedCandidate:
        return QueuedCandidate(
            key=row.posting_key, url=row.url, case_id=case_id_for(row.posting_key)
        )

    return DiscoveryQueues(
        board_to_screen=[r.posting_key for r in sorted(board_screen, key=newest_first)],
        search_to_screen=[
            r.posting_key
            for r in sorted(search_screen, key=lambda r: (repo.ensure_utc(r.first_seen_at), r.seq))
        ],
        board_passed=[queued(r) for r in sorted(board_passed, key=newest_first)],
        search_passed=[
            queued(r)
            for r in sorted(
                search_passed,
                key=lambda r: (repo.ensure_utc(r.first_passed_at or r.first_seen_at), r.seq),
            )
        ],
    )


@activity.defn(name="screen_candidate")
def screen_candidate(payload: ScreenInput) -> ScreenOutcome:
    """Screen one candidate against the profile and store the verdict.

    A model-level failure passes the candidate (ADR-017), recorded as such.
    """

    profile = load_operator_profile()
    fingerprint = profile_fingerprint(profile)
    with session_scope() as session:
        row = discovery.get_candidate(session, payload.key)
        if row is None:
            raise RuntimeError(f"unknown candidate {payload.key!r}")
        facts = ListingFacts(
            title=row.title,
            company=row.company,
            locations=list(row.locations),
            department=row.department,
            team=row.team,
            excerpt=row.excerpt,
        )
    result = screen_posting(resolve_discovery_provider(), profile, facts)
    with session_scope() as session:
        row = discovery.get_candidate(session, payload.key)
        if row is None:
            raise RuntimeError(f"unknown candidate {payload.key!r}")
        discovery.record_verdict(
            session,
            row,
            decision=result.decision,
            reason=result.reason,
            failed_open=result.failed_open,
            inputs=_current(row, fingerprint, discovery_route()),
            usage=result.usage.model_dump(mode="json") if result.usage else None,
            at=repo.utcnow(),
        )
    return ScreenOutcome(
        decision=result.decision, failed_open=result.failed_open, model_calls=result.model_calls
    )


DISCOVERY_ACTIVITIES = [derive_brief, search_postings, discovery_queues, screen_candidate]
