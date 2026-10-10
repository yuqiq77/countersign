"""Canonical enums whose spelling must not drift across system boundaries."""

from enum import StrEnum


class JobStatus(StrEnum):
    """Observed application status after authority-aware verification."""

    OPEN = "OPEN"
    OPEN_THIRD_PARTY_ONLY = "OPEN_THIRD_PARTY_ONLY"
    UNVERIFIED = "UNVERIFIED"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class ApplyEntryKind(StrEnum):
    """How the reader identified an application entry; NONE when it found none."""

    NONE = "NONE"
    JSON_LD_DIRECT_APPLY = "JSON_LD_DIRECT_APPLY"
    ATS_LINK = "ATS_LINK"
    APPLY_CONTROL = "APPLY_CONTROL"
    MAILTO = "MAILTO"
    TEXT_MARKER = "TEXT_MARKER"


class EvidenceKind(StrEnum):
    """What a cited page span proves about the posting's status."""

    CLOSED_NOTICE = "CLOSED_NOTICE"
    VALID_THROUGH = "VALID_THROUGH"


class ObservationHow(StrEnum):
    """How one observation was obtained; API is an ATS adapter's response (ticket 003)."""

    STATIC = "STATIC"
    RENDERED = "RENDERED"
    FOLLOWED = "FOLLOWED"
    API = "API"


class ReaderAction(StrEnum):
    """The only moves the page reader may request; code executes them."""

    DONE = "DONE"
    RENDER = "RENDER"
    FOLLOW = "FOLLOW"


class ReaderStop(StrEnum):
    """Why a page reading ended — always recorded, never inferred."""

    DONE = "DONE"
    STEP_BUDGET = "STEP_BUDGET"
    WALL_CLOCK = "WALL_CLOCK"
    MODEL_FAILURE = "MODEL_FAILURE"


class SourceAuthority(StrEnum):
    """Authority tier used to resolve conflicting source observations."""

    OFFICIAL_ATS = "OFFICIAL_ATS"
    OFFICIAL_COMPANY = "OFFICIAL_COMPANY"
    COMPANY_CONTROLLED = "COMPANY_CONTROLLED"
    TRUSTED_BOARD_REDIRECT = "TRUSTED_BOARD_REDIRECT"
    DISCOVERY_ONLY = "DISCOVERY_ONLY"


class InvocationSource(StrEnum):
    """Actors permitted by a capability definition."""

    WORKFLOW = "WORKFLOW"
    MODEL = "MODEL"
    HUMAN = "HUMAN"


class CaseState(StrEnum):
    """Business lifecycle states of a Job Intelligence Case (v4 §9)."""

    DISCOVERED = "DISCOVERED"
    SNAPSHOTTED = "SNAPSHOTTED"
    NORMALIZED = "NORMALIZED"
    DEDUPLICATED = "DEDUPLICATED"
    OFFICIAL_SOURCE_VERIFIED = "OFFICIAL_SOURCE_VERIFIED"
    EVALUATED = "EVALUATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
    SUBMITTED = "SUBMITTED"
    INTERVIEWING = "INTERVIEWING"
    NO_RESPONSE = "NO_RESPONSE"
    WITHDRAWN = "WITHDRAWN"
    OFFERED = "OFFERED"
    OUTCOME_REJECTED = "OUTCOME_REJECTED"
    HIRED = "HIRED"
    QUARANTINED = "QUARANTINED"
    CLOSED_OR_STALE = "CLOSED_OR_STALE"


class EvalDecision(StrEnum):
    """Recommendation bands of the evaluation policy."""

    APPLY_NOW = "APPLY_NOW"
    STRETCH = "STRETCH"
    WAIT = "WAIT"
    NETWORK = "NETWORK"
    BENCHMARK = "BENCHMARK"
    SKIP = "SKIP"


class SeniorityFit(StrEnum):
    """Seniority classification relative to the operator profile."""

    REALISTIC = "REALISTIC"
    PLAUSIBLE_STRETCH = "PLAUSIBLE_STRETCH"
    LOW_PROBABILITY_STRETCH = "LOW_PROBABILITY_STRETCH"
    BENCHMARK_ONLY = "BENCHMARK_ONLY"
    SKIP = "SKIP"


class SalaryPeriod(StrEnum):
    """The pay period a posting states for its salary figures."""

    ANNUAL = "ANNUAL"
    MONTHLY = "MONTHLY"
    HOURLY = "HOURLY"
    UNKNOWN = "UNKNOWN"


class CompensationSignal(StrEnum):
    """Deterministically computed salary attractiveness label."""

    ATTRACTIVE = "ATTRACTIVE"
    ACCEPTABLE = "ACCEPTABLE"
    CONCERNING = "CONCERNING"
    UNKNOWN = "UNKNOWN"


class Severity(StrEnum):
    """Severity scale shared by blockers and risk flags."""

    HARD = "HARD"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Relevance(StrEnum):
    """Relevance scale for distinctive assets."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ApprovalDecision(StrEnum):
    """Decisions a human reviewer may durably record."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"


class ApprovalRequestStatus(StrEnum):
    """Lifecycle of one approval request."""

    PENDING = "PENDING"
    DECIDED = "DECIDED"
    EXPIRED = "EXPIRED"


class ActorType(StrEnum):
    """Actor categories recorded on audit events."""

    HUMAN = "HUMAN"
    SERVICE = "SERVICE"
    SYSTEM = "SYSTEM"
