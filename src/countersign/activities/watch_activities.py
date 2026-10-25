"""Temporal Activities of the scheduled watch (ticket 004, ADR-016, ADR-017).

The watch reads the operator's ATS boards, records their postings as
discovery candidates, admits screened candidates as cases and records its
run; rechecks of pending cases go through the verify path
(`case_activities.recheck_case`), and the search and the screen live in
`discovery_activities`. Every write is idempotent: a case id derives from
its posting, an audit id from its case or its run.
"""

import json
import uuid
from datetime import datetime, timedelta

from temporalio import activity
from temporalio.exceptions import ApplicationError

from countersign.capabilities.ats import ListedPosting, ashby, greenhouse
from countersign.capabilities.http_fetch import FetchResult, fetch_url
from countersign.contracts.enums import (
    ActorType,
    CandidateSource,
    CaseState,
    JobStatus,
)
from countersign.contracts.workflow_io import (
    DiscoverInput,
    DiscoveryOutcome,
    NewCases,
    RecheckInput,
    WatchPlan,
    WatchRunStart,
    WatchRunSummary,
)
from countersign.domain.discovery import other_source, url_key
from countersign.gateway.discovery import BRIEF_PROMPT_VERSION
from countersign.gateway.evaluator import profile_fingerprint
from countersign.persistence import discovery, repo
from countersign.persistence.db import session_scope
from countersign.policy.operator_profile import load_operator_profile
from countersign.policy.watchlist import WATCHLIST_PATH_VAR, load_watchlist, passes_filter

# Fixed for good: a posting's case id must never change.
_POSTING_NAMESPACE = uuid.UUID("2d8b60f0-8d41-4210-ba04-8c45ba22607c")

_LISTERS = {"ashby": ashby.list_board, "greenhouse": greenhouse.list_board}


def posting_key(url: str) -> str | None:
    """The watch's identity of a posting URL, or None for any other URL.

    Ashby: board and job id. Greenhouse: the job id, under either hosted
    spelling. A pasted URL that redirects onto a posting is not recognised.
    """

    ref = ashby.posting_ref(url)
    if ref is not None:
        return f"ashby:{ref[0].lower()}:{ref[1].lower()}"
    ref = greenhouse.posting_ref(url)
    if ref is not None:
        return f"greenhouse:{ref[1]}"
    return None


def candidate_key(url: str) -> str:
    """Any posting's identity: the ATS key where the URL parses, else the URL (ADR-017)."""

    return posting_key(url) or url_key(url)


def case_id_for(key: str) -> str:
    """The case id of a posting the watch admits: a retried Activity or a
    replayed run finds the same case instead of creating a second one."""

    return str(uuid.uuid5(_POSTING_NAMESPACE, key))


def _workflow_id() -> str | None:
    try:
        return activity.info().workflow_id
    except RuntimeError:  # outside an activity context (unit tests)
        return None


def _fetch_board(url: str) -> FetchResult:
    # An Ashby board carries every description: the adapter's own cap.
    return fetch_url(url, max_body_bytes=ashby.MAX_BOARD_BYTES)


@activity.defn(name="plan_watch_run")
def plan_watch_run(start: WatchRunStart) -> WatchPlan:
    """Load the watchlist once for the run and list the cases due a recheck."""

    try:
        policy = load_watchlist()
    except ValueError as exc:
        raise ApplicationError(str(exc), type="PolicyError", non_retryable=True) from exc
    if policy is None:
        raise ApplicationError(
            f"{WATCHLIST_PATH_VAR} is unset: no watch", type="PolicyError", non_retryable=True
        )
    try:
        fingerprint = profile_fingerprint(load_operator_profile())
    except ValueError as exc:
        raise ApplicationError(str(exc), type="PolicyError", non_retryable=True) from exc
    with session_scope() as session:
        rechecks = [
            RecheckInput(
                case_id=row.id,
                url=row.url,
                generation=approval.generation,
                approval_request_id=approval.id,
                run_key=start.run_key,
            )
            for row, approval in repo.recheck_candidates(session, start.started_at)
        ]
        brief = (
            discovery.get_brief(session, fingerprint, BRIEF_PROMPT_VERSION)
            if fingerprint != "none"
            else None
        )
        previous = repo.latest_audit_reason(session, "watch.run")
    return WatchPlan(
        watchlist=policy.watchlist,
        fingerprint=policy.fingerprint,
        rechecks=rechecks,
        profile_fingerprint=fingerprint,
        brief=brief,
        first_source=_next_first(previous),
    )


def _next_first(previous_run: str | None) -> CandidateSource:
    """The source the last run let go second takes the first turn now."""

    try:
        recorded = json.loads(previous_run or "{}").get("first_source")
        return other_source(CandidateSource(recorded))
    except (ValueError, AttributeError):
        return CandidateSource.BOARD


@activity.defn(name="discover_postings")
def discover_postings(payload: DiscoverInput) -> DiscoveryOutcome:
    """Read every watched board once and record its postings as candidates.

    A posting some case already covers, in any state, is known and never
    admitted again. When that case ended by source closure and the board
    lists the posting as published after the closure, the case gets one
    `case.reappeared` event, whatever the gap. Every other posting that
    passes the filter and was published within the window is recorded, or
    seen again, as a board candidate (ADR-017); the screen and the turns
    decide what is admitted.
    """

    watchlist = payload.watchlist
    since = payload.started_at - timedelta(days=watchlist.published_within_days)
    known: dict[str, list[str]] = {}
    closed_at: dict[str, datetime] = {}
    with session_scope() as session:
        for row in repo.list_cases(session):
            key = posting_key(row.url)
            if key is None:
                continue
            known.setdefault(key, []).append(row.id)
            if row.state == CaseState.CLOSED_OR_STALE.value:
                latest = repo.latest_verification(session, row.id)
                if latest is not None and latest.status in (JobStatus.CLOSED, JobStatus.EXPIRED):
                    closed_at[row.id] = latest.checked_at

    boards: list[str] = []
    candidates: list[tuple[str, str, ListedPosting]] = []
    returned: list[tuple[str, ListedPosting]] = []
    seen = filtered = without_location = known_count = outside_window = 0
    for board in watchlist.boards:
        listing = _LISTERS[board.adapter](board.board, fetch=_fetch_board)
        boards.append(f"{board.adapter}/{board.board}: {listing.note}")
        for posting in listing.postings or []:
            seen += 1
            key = posting_key(posting.url)
            assert key is not None  # the adapters list hosted posting URLs
            if key in known:
                known_count += 1
                returned.extend(
                    (case_id, posting)
                    for case_id in known[key]
                    if case_id in closed_at and posting.published_at > closed_at[case_id]
                )
                continue
            if not passes_filter(watchlist, posting.title, posting.locations):
                filtered += 1
                continue
            if posting.published_at < since:
                outside_window += 1
                continue
            without_location += not posting.locations
            candidates.append((f"{board.adapter}/{board.board}", key, posting))

    reappeared = 0
    if returned:
        with session_scope() as session:
            for case_id, posting in returned:
                reappeared += repo.append_audit(
                    session,
                    # One per closed case: a Greenhouse edit moves the same
                    # timestamp a republication does.
                    audit_id=f"aud-case-reappeared-{case_id}",
                    actor_type=ActorType.SYSTEM,
                    actor_id="watch",
                    action="case.reappeared",
                    resource_type="job_case",
                    resource_id=case_id,
                    reason=f"listed again, published {posting.published_at.isoformat()}",
                    workflow_id=_workflow_id(),
                )

    recorded = 0
    with session_scope() as session:
        for origin, key, posting in candidates:
            _, created = discovery.record_sighting(
                session,
                posting_key=key,
                url=posting.url,
                source=CandidateSource.BOARD,
                origin=origin,
                title=posting.title,
                # The board is the company's own: its slug, as listed.
                company=origin.split("/", 1)[1],
                locations=list(posting.locations),
                seen_at=payload.started_at,
                department=posting.department,
                team=posting.team,
                excerpt=posting.excerpt,
                published_at=posting.published_at,
            )
            recorded += created
    return DiscoveryOutcome(
        boards=boards,
        seen=seen,
        filtered=filtered,
        without_location=without_location,
        known=known_count,
        outside_window=outside_window,
        reappeared=reappeared,
        recorded=recorded,
    )


@activity.defn(name="create_watch_cases")
def create_watch_cases(payload: NewCases) -> None:
    """Create the admitted cases in DISCOVERED with actor `watch` (idempotent by id)
    and mark their candidates admitted."""

    with session_scope() as session:
        for posting in payload.postings:
            repo.create_case(
                session,
                case_id=posting.case_id,
                url=posting.url,
                request_id=payload.run_key,
                actor_type=ActorType.SYSTEM,
                actor_id="watch",
            )
            if posting.candidate_key is not None:
                discovery.mark_admitted(session, posting.candidate_key, posting.case_id)


@activity.defn(name="record_watch_run")
def record_watch_run(summary: WatchRunSummary) -> None:
    """Append the run's `watch.run` audit event: watchlist identity and counts."""

    with session_scope() as session:
        repo.append_audit(
            session,
            audit_id=f"aud-watch-run-{summary.run_key}",
            actor_type=ActorType.SYSTEM,
            actor_id="watch",
            action="watch.run",
            resource_type="watch_run",
            resource_id=summary.run_key,
            reason=summary.model_dump_json(),
            workflow_id=_workflow_id(),
            request_id=summary.run_key,
        )
    # Counts only: board names are operator-private.
    activity.logger.info(
        "watch run %s: watchlist %s, %d boards, %d seen, %d created, %d rechecked, %d closed",
        summary.run_key,
        summary.watchlist_fingerprint,
        len(summary.boards),
        summary.seen,
        summary.created,
        summary.rechecked,
        summary.closed,
    )


WATCH_ACTIVITIES = [plan_watch_run, discover_postings, create_watch_cases, record_watch_run]
