"""Typed inputs/outputs crossing the Temporal workflow/Activity boundary.

Payloads carry references and scalar facts, never raw bodies (v4 §8.4):
everything here is serialized into workflow history.
"""

from datetime import datetime

from pydantic import Field

from countersign.contracts.base import ContractModel
from countersign.contracts.discovery import SearchBrief
from countersign.contracts.enums import (
    ApprovalDecision,
    CandidateSource,
    CaseState,
    JobStatus,
    ScreenDecision,
)
from countersign.contracts.evaluation import EvaluationRecord
from countersign.contracts.provenance import SnapshotRecord
from countersign.contracts.verification import VerificationResult
from countersign.contracts.watchlist import Watchlist


class CaseWorkflowInput(ContractModel):
    """Input to JobIntelligenceWorkflow; stable across contract versions."""

    case_id: str
    url: str
    generation: int = 1
    contract_version: str = "1"


class SnapshotOutcome(ContractModel):
    """Result of the snapshot Activity (provenance metadata, no body)."""

    snapshot: SnapshotRecord


class NormalizeOutcome(ContractModel):
    """Result of the normalize Activity: scalar facts, never the full text."""

    title: str | None
    company_hint: str | None
    content_hash: str
    extractor_version: str


class VerifyOutcome(ContractModel):
    """Result of the verify Activity."""

    verification: VerificationResult
    # Length of the normalized posting text in the verified observation;
    # zero means there is nothing to evaluate and the workflow stops before
    # the model (NEEDS_MORE_EVIDENCE), never spending a call on an empty body.
    # Old Temporal histories lack this field; None preserves their original path.
    observed_text_chars: int | None = None


class EvaluateOutcome(ContractModel):
    """Result of the evaluate Activity."""

    record: EvaluationRecord


class ApprovalRequestOutcome(ContractModel):
    """Result of the create-approval-request Activity.

    `expires_at` is always timezone-aware UTC; the Temporal pydantic
    converter round-trips it natively (no manual ISO stringification).
    """

    approval_request_id: str
    expires_at: datetime


class DecisionSignal(ContractModel):
    """The approval Signal payload; idempotent by request id + version."""

    approval_request_id: str
    decision: ApprovalDecision
    reason: str
    decision_version: int
    actor: str


class CaseStateNote(ContractModel):
    """Input for terminal bookkeeping Activities (quarantine, close, expire)."""

    case_id: str
    reason: str
    approval_request_id: str | None = None


class CaseWorkflowStatus(ContractModel):
    """Query response exposing workflow progress without changing it."""

    case_id: str
    state: CaseState
    approval_request_id: str | None
    decided: bool
    generation: int = 1


class CaseWorkflowResult(ContractModel):
    """Terminal summary returned by the workflow."""

    case_id: str
    final_state: CaseState
    decision: ApprovalDecision | None


# --- scheduled watch (ticket 004, ADR-016) ---------------------------------


class SourceClosedSignal(ContractModel):
    """A recheck found the posting closed while its approval request waited."""

    approval_request_id: str
    reason: str


class WatchRunStart(ContractModel):
    """Input of the watch run's first Activity."""

    run_key: str
    started_at: datetime


class RecheckInput(ContractModel):
    """One pending case to recheck, with the approval request it waits on."""

    case_id: str
    url: str
    generation: int
    approval_request_id: str
    run_key: str


class WatchPlan(ContractModel):
    """The watchlist revision this run uses and the cases due a recheck, oldest first.

    For discovery (ADR-017): the profile's fingerprint ("none" without a
    profile), the brief stored for it, if any, and the source that takes
    the first turn in this run.
    """

    watchlist: Watchlist
    fingerprint: str
    rechecks: list[RecheckInput]
    profile_fingerprint: str = "none"
    brief: SearchBrief | None = None
    first_source: CandidateSource = CandidateSource.BOARD


class RecheckOutcome(ContractModel):
    """What a recheck recorded and the model calls it made.

    `recorded` is False when the case no longer waited on the approval
    request, in which case nothing was written.
    """

    recorded: bool
    model_calls: int
    status: JobStatus | None = None
    reason: str = ""


class DiscoverInput(ContractModel):
    watchlist: Watchlist
    started_at: datetime


class NewPosting(ContractModel):
    """A posting admitted as a case: its posting-derived case id, URL and candidate."""

    case_id: str
    url: str
    candidate_key: str | None = None


class DiscoveryOutcome(ContractModel):
    """One read of every watched board: outcomes and counts. The postings
    themselves are recorded as candidates (ADR-017), not carried here."""

    boards: list[str]
    seen: int
    filtered: int
    without_location: int
    known: int
    outside_window: int
    reappeared: int
    recorded: int


class BriefInput(ContractModel):
    run_key: str
    profile_fingerprint: str


class BriefOutcome(ContractModel):
    """The brief derived for the run's profile, or why there is none."""

    brief: SearchBrief | None
    failure: str | None
    model_calls: int


class SearchInput(ContractModel):
    """The search step's input: the brief and both caps (ADR-017)."""

    run_key: str
    started_at: datetime
    brief: SearchBrief
    regions: list[str]
    max_searches: int
    max_requests: int


class SearchOutcome(ContractModel):
    """What the search step recorded and spent, or why it stopped."""

    found: int
    new: int
    leads: int
    rejected: int
    searches: int
    model_calls: int
    failure: str | None


class QueueInput(ContractModel):
    run_key: str
    started_at: datetime
    profile_fingerprint: str
    published_within_days: int


class QueuedCandidate(ContractModel):
    """A candidate that passed the screen, ready to become a case."""

    key: str
    url: str
    case_id: str


class DiscoveryQueues(ContractModel):
    """Each source's candidates to screen and to admit, in its own order."""

    board_to_screen: list[str]
    search_to_screen: list[str]
    board_passed: list[QueuedCandidate]
    search_passed: list[QueuedCandidate]


class ScreenInput(ContractModel):
    run_key: str
    key: str
    profile_fingerprint: str


class ScreenOutcome(ContractModel):
    decision: ScreenDecision
    failed_open: bool
    model_calls: int


class NewCases(ContractModel):
    run_key: str
    postings: list[NewPosting]


class WatchRunSummary(ContractModel):
    """The run's trail, written as its `watch.run` audit event."""

    run_key: str
    watchlist_version: str
    watchlist_fingerprint: str
    boards: list[str]
    seen: int
    filtered: int
    without_location: int
    known: int
    outside_window: int
    reappeared: int
    created: int
    rechecked: int
    rechecked_without_model: int
    closed: int
    recheck_failures: int
    recheck_model_calls: int
    new_case_model_calls: int
    # Discovery (ADR-017); defaults keep earlier summaries readable.
    # Searches charged: each response's own count, a failed request's whole
    # allowance, and the whole cap when the step's outcome was lost.
    searches: int = 0
    search_found: int = 0
    search_rejected: int = 0
    leads: int = 0
    screened_pass: int = 0
    screened_drop: int = 0
    screened_failed_open: int = 0
    screen_failures: int = 0
    board_waiting: int = 0
    search_waiting: int = 0
    created_from_search: int = 0
    discovery_model_calls: int = 0
    first_source: CandidateSource = CandidateSource.BOARD
    failures: list[str] = Field(default_factory=lambda: [])
