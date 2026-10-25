"""Discovery's Activities on the database (ADR-017, ticket 005): the brief, the
search's records, the queues and the screen, staleness after a profile
change, and the verification reason when a fetch lands on a listed host."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

import countersign.activities.case_activities as case_activities
import countersign.activities.discovery_activities as discovery_activities
import countersign.activities.watch_activities as watch_activities
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.discovery import (
    ScreenVerdict,
    SearchBrief,
    SearchCandidate,
    SearchCandidates,
)
from countersign.contracts.enums import CandidateStatus, ScreenDecision
from countersign.contracts.workflow_io import (
    BriefInput,
    CaseWorkflowInput,
    NewCases,
    NewPosting,
    QueueInput,
    ScreenInput,
    SearchInput,
)
from countersign.gateway.discovery import BRIEF_PROMPT_VERSION, BriefResult
from countersign.gateway.evaluator import profile_fingerprint
from countersign.gateway.fake_provider import (
    FAKE_BRIEF,
    FakeEvaluationProvider,
    fake_search_response,
)
from countersign.persistence import discovery, repo
from countersign.persistence.db import session_scope
from countersign.persistence.models import DiscoverySearchRow
from countersign.policy.operator_profile import PROFILE_PATH_VAR

NOW = datetime(2026, 10, 22, 7, 0, tzinfo=UTC)
ASHBY = "https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555"
CAREERS = "https://careers.example.com/jobs/7?utm_source=search"
LINKEDIN = "https://www.linkedin.com/jobs/view/9"
INVENTED = "https://invented.example/job"


def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> str:
    path = tmp_path / "profile.md"
    path.write_text(text)
    monkeypatch.setenv(PROFILE_PATH_VAR, str(path))
    return profile_fingerprint(text)


def _candidate(url: str, title: str = "Platform Engineer") -> SearchCandidate:
    return SearchCandidate(url=url, title=title, company="Acme", location="Toronto", reason="fits")


def _search(provider: FakeEvaluationProvider, run_key: str, at: datetime) -> None:
    outcome = discovery_activities.search_postings(
        SearchInput(
            run_key=run_key,
            started_at=at,
            brief=FAKE_BRIEF,
            regions=["Toronto"],
            max_searches=4,
            max_requests=3,
        )
    )
    assert outcome.failure is None


def _queues(fingerprint: str, at: datetime) -> tuple[list[str], list[str]]:
    queues = discovery_activities.discovery_queues(
        QueueInput(
            run_key="q", started_at=at, profile_fingerprint=fingerprint, published_within_days=14
        )
    )
    assert queues.board_to_screen == [] and queues.board_passed == []
    return queues.search_to_screen, [c.key for c in queues.search_passed]


@pytest.mark.usefixtures("db")
def test_the_brief_is_derived_for_the_runs_profile_and_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fingerprint = _profile(tmp_path, monkeypatch, "Seeks platform roles.")
    monkeypatch.setattr(
        discovery_activities, "resolve_discovery_provider", lambda: FakeEvaluationProvider()
    )
    outcome = discovery_activities.derive_brief(
        BriefInput(run_key="r1", profile_fingerprint=fingerprint)
    )
    assert (outcome.brief, outcome.failure, outcome.model_calls) == (FAKE_BRIEF, None, 1)
    with session_scope() as session:
        stored = discovery.latest_brief(session)
    assert stored is not None and stored.profile_fingerprint == fingerprint
    changed = discovery_activities.derive_brief(
        BriefInput(run_key="r2", profile_fingerprint="0000")
    )
    assert (changed.brief, changed.model_calls) == (None, 0)


def _brief(role: str) -> SearchBrief:
    return FAKE_BRIEF.model_copy(update={"role_families": [role]})


@pytest.mark.usefixtures("db")
def test_a_retried_brief_returns_the_stored_one_without_calling_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket 005 review: the first attempt stored its brief and its
    completion was lost. The retry returns that brief, the one the record
    and the UI show, with the calls that attempt made."""

    fingerprint = _profile(tmp_path, monkeypatch, "Seeks backend roles.")
    run = BriefInput(run_key="r1", profile_fingerprint=fingerprint)
    # One contract failure, then the brief: two calls.
    first = FakeEvaluationProvider(brief=_brief("Backend Engineer"), fail_attempts=1)
    monkeypatch.setattr(discovery_activities, "resolve_discovery_provider", lambda: first)
    assert discovery_activities.derive_brief(run).model_calls == 2
    retry = FakeEvaluationProvider(brief=_brief("Platform Engineer"))
    monkeypatch.setattr(discovery_activities, "resolve_discovery_provider", lambda: retry)
    outcome = discovery_activities.derive_brief(run)
    assert (outcome.brief, outcome.model_calls, retry.calls) == (_brief("Backend Engineer"), 2, 0)
    with session_scope() as session:
        shown = discovery.latest_brief(session)
    assert shown is not None and shown.brief == outcome.brief


@pytest.mark.usefixtures("db")
def test_a_brief_stored_while_an_attempt_derives_is_the_one_it_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An earlier attempt that outlived its timeout stores its brief while
    this one derives: this one keeps the stored brief and returns it."""

    fingerprint = _profile(tmp_path, monkeypatch, "Seeks backend roles.")

    def overtaken(provider: object, profile: str) -> BriefResult:
        with session_scope() as session:
            discovery.save_brief(
                session,
                profile_fingerprint=fingerprint,
                prompt_version=BRIEF_PROMPT_VERSION,
                route="fake",
                brief=_brief("Backend Engineer"),
                usage=None,
            )
        return BriefResult(_brief("Platform Engineer"), None, 1)

    monkeypatch.setattr(discovery_activities, "derive_search_brief", overtaken)
    outcome = discovery_activities.derive_brief(
        BriefInput(run_key="r1", profile_fingerprint=fingerprint)
    )
    assert (outcome.brief, outcome.model_calls) == (_brief("Backend Engineer"), 1)


@pytest.mark.usefixtures("db")
def test_the_search_records_candidates_leads_and_every_requests_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(tmp_path, monkeypatch, "Seeks platform roles.")
    paused = fake_search_response(
        results=[("site:ashby", ASHBY)], searches=2, stop_reason="pause_turn"
    )
    final = fake_search_response(
        SearchCandidates(
            candidates=[
                _candidate(ASHBY),
                _candidate(CAREERS),
                _candidate(LINKEDIN),
                _candidate(INVENTED),
            ]
        ),
        results=[("careers", CAREERS), ("linkedin", LINKEDIN)],
        searches=1,
    )
    provider = FakeEvaluationProvider(searches=[paused, final])
    monkeypatch.setattr(discovery_activities, "resolve_discovery_provider", lambda: provider)
    with session_scope() as session:
        repo.create_case(
            session, case_id=str(uuid.uuid4()), url="https://careers.example.com/jobs/7"
        )

    outcome = discovery_activities.search_postings(
        SearchInput(
            run_key="run1",
            started_at=NOW,
            brief=FAKE_BRIEF,
            regions=["Toronto"],
            max_searches=4,
            max_requests=3,
        )
    )
    assert (outcome.found, outcome.new, outcome.leads, outcome.rejected) == (2, 2, 1, 1)
    assert (outcome.searches, outcome.model_calls, outcome.failure) == (3, 2, None)
    with session_scope() as session:
        searches = list(session.scalars(select(DiscoverySearchRow).order_by(DiscoverySearchRow.id)))
        assert [(r.request_index, r.queries, r.searches) for r in searches] == [
            (1, ["site:ashby"], 2),
            (2, ["careers", "linkedin"], 1),
        ]
        ashby_row = discovery.get_candidate(session, watch_activities.candidate_key(ASHBY))
        lead = discovery.get_candidate(session, watch_activities.candidate_key(LINKEDIN))
        covered = discovery.get_candidate(session, watch_activities.candidate_key(CAREERS))
        assert ashby_row is not None and ashby_row.status == CandidateStatus.UNSCREENED.value
        assert ashby_row.origin == "site:ashby" and ashby_row.reason == "fits"
        assert lead is not None and lead.status == CandidateStatus.LEAD.value
        assert covered is None  # a case already covers it


@pytest.mark.usefixtures("db")
def test_screening_orders_the_queues_and_a_profile_change_makes_verdicts_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_fp = _profile(tmp_path, monkeypatch, "Seeks platform roles.")
    keep, drop, old_drop = (f"https://jobs.ashbyhq.com/acme/{n}" for n in ("keep", "drop", "old"))
    provider = FakeEvaluationProvider(
        searches=[
            fake_search_response(
                SearchCandidates(candidates=[_candidate(old_drop)]), results=[("q", old_drop)]
            ),
            fake_search_response(
                SearchCandidates(candidates=[_candidate(keep), _candidate(drop)]),
                results=[("q", keep), ("q", drop)],
            ),
        ],
        verdicts=[
            ScreenVerdict(verdict=ScreenDecision.DROP, reason="sales"),
            ScreenVerdict(verdict=ScreenDecision.PASS, reason="platform"),
            ScreenVerdict(verdict=ScreenDecision.DROP, reason="legal"),
        ],
    )
    monkeypatch.setattr(discovery_activities, "resolve_discovery_provider", lambda: provider)
    early = NOW - timedelta(days=30)
    _search(provider, "run0", early)
    _search(provider, "run1", NOW)
    keys = [watch_activities.candidate_key(u) for u in (old_drop, keep, drop)]

    to_screen, passed = _queues(first_fp, NOW)
    assert (to_screen, passed) == (keys, [])  # first seen, oldest first
    for key in keys:
        discovery_activities.screen_candidate(
            ScreenInput(run_key="run1", key=key, profile_fingerprint=first_fp)
        )
    assert _queues(first_fp, NOW) == ([], [keys[1]])

    second_fp = _profile(tmp_path, monkeypatch, "Seeks platform and data roles.")
    # The pass and the recent drop are screened again; the drop last seen a
    # month ago is not, until a search returns it.
    assert _queues(second_fp, NOW) == ([keys[1], keys[2]], [])


@pytest.mark.usefixtures("db")
def test_a_drop_that_passes_later_joins_the_back_and_admission_is_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fingerprint = _profile(tmp_path, monkeypatch, "Seeks platform roles.")
    first, second = (f"https://jobs.ashbyhq.com/acme/{n}" for n in ("first", "second"))
    provider = FakeEvaluationProvider(
        searches=[
            fake_search_response(
                SearchCandidates(candidates=[_candidate(first), _candidate(second)]),
                results=[("q", first), ("q", second)],
            )
        ],
        verdicts=[
            ScreenVerdict(verdict=ScreenDecision.DROP, reason="unclear"),
            ScreenVerdict(verdict=ScreenDecision.PASS, reason="platform"),
            ScreenVerdict(verdict=ScreenDecision.PASS, reason="platform after all"),
        ],
    )
    monkeypatch.setattr(discovery_activities, "resolve_discovery_provider", lambda: provider)
    _search(provider, "run1", NOW)
    k1, k2 = (watch_activities.candidate_key(u) for u in (first, second))
    for key in (k1, k2):
        discovery_activities.screen_candidate(
            ScreenInput(run_key="run1", key=key, profile_fingerprint=fingerprint)
        )
    with session_scope() as session:
        row = discovery.get_candidate(session, k1)
        assert row is not None
        row.verdict_listing_hash = "stale"  # as if its listing had changed
    discovery_activities.screen_candidate(
        ScreenInput(run_key="run2", key=k1, profile_fingerprint=fingerprint)
    )
    assert _queues(fingerprint, NOW) == ([], [k2, k1])

    case_id = watch_activities.case_id_for(k2)
    watch_activities.create_watch_cases(
        NewCases(
            run_key="run2", postings=[NewPosting(case_id=case_id, url=second, candidate_key=k2)]
        )
    )
    with session_scope() as session:
        row = discovery.get_candidate(session, k2)
        assert row is not None and (row.status, row.case_id) == ("ADMITTED", case_id)
    assert _queues(fingerprint, NOW) == ([], [k1])


@pytest.mark.usefixtures("db")
def test_a_verification_that_lands_on_a_listed_host_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A case's own fetch follows a redirect onto such a host (ADR-017); the
    reason says so, and the reader neither renders nor follows there."""

    start = "https://careers.example.com/jobs/7"

    def fetch(url: str, **_: object) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=LINKEDIN,
            http_status=200,
            content_type="text/html",
            body=b"<html><body><h1>Platform Engineer</h1><p>Sign in to apply.</p></body></html>",
            redirect_chain=[url],
        )

    monkeypatch.setattr(case_activities, "fetch_url", fetch)
    monkeypatch.setattr(case_activities, "resolve_provider", FakeEvaluationProvider)
    payload = CaseWorkflowInput(case_id=str(uuid.uuid4()), url=start)
    with session_scope() as session:
        repo.create_case(session, case_id=payload.case_id, url=start)
    case_activities.snapshot_source(payload)
    case_activities.normalize_case(payload)
    outcome = case_activities.verify_status(payload)
    assert outcome.verification.reason.endswith(
        "reached linkedin.com, whose terms forbid automated access"
    )
