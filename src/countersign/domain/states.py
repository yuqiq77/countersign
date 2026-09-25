"""Case state machine: allowed transitions and guards (v4 §9).

Pure logic — no I/O. Operational conditions (retries, schema repair,
manual intervention) live on run records, never here.
"""

from countersign.contracts.enums import ApprovalDecision, CaseState


class TransitionError(Exception):
    """Raised when a state transition is not allowed by the state machine."""

    def __init__(self, current: CaseState, requested: CaseState) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"illegal transition {current.value} -> {requested.value}")


_QUARANTINABLE: frozenset[CaseState] = frozenset(
    {
        CaseState.DISCOVERED,
        CaseState.SNAPSHOTTED,
        CaseState.NORMALIZED,
        CaseState.DEDUPLICATED,
        CaseState.OFFICIAL_SOURCE_VERIFIED,
        CaseState.EVALUATED,
        CaseState.NEEDS_MORE_EVIDENCE,
    }
)

ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.DISCOVERED: frozenset({CaseState.SNAPSHOTTED, CaseState.QUARANTINED}),
    CaseState.SNAPSHOTTED: frozenset({CaseState.NORMALIZED, CaseState.QUARANTINED}),
    CaseState.NORMALIZED: frozenset({CaseState.DEDUPLICATED, CaseState.QUARANTINED}),
    CaseState.DEDUPLICATED: frozenset({CaseState.OFFICIAL_SOURCE_VERIFIED, CaseState.QUARANTINED}),
    CaseState.OFFICIAL_SOURCE_VERIFIED: frozenset(
        {
            CaseState.EVALUATED,
            CaseState.NEEDS_MORE_EVIDENCE,
            CaseState.CLOSED_OR_STALE,
            CaseState.QUARANTINED,
        }
    ),
    CaseState.EVALUATED: frozenset(
        {
            CaseState.PENDING_APPROVAL,
            CaseState.NEEDS_MORE_EVIDENCE,
            CaseState.CLOSED_OR_STALE,
            CaseState.QUARANTINED,
        }
    ),
    CaseState.PENDING_APPROVAL: frozenset(
        {
            CaseState.APPROVED,
            CaseState.REJECTED,
            CaseState.NEEDS_MORE_EVIDENCE,
            CaseState.CLOSED_OR_STALE,
        }
    ),
    CaseState.NEEDS_MORE_EVIDENCE: frozenset(
        {CaseState.OFFICIAL_SOURCE_VERIFIED, CaseState.QUARANTINED}
    ),
    CaseState.APPROVED: frozenset({CaseState.SUBMITTED}),
    CaseState.SUBMITTED: frozenset(
        {CaseState.INTERVIEWING, CaseState.NO_RESPONSE, CaseState.WITHDRAWN}
    ),
    CaseState.INTERVIEWING: frozenset({CaseState.OFFERED, CaseState.OUTCOME_REJECTED}),
    CaseState.OFFERED: frozenset({CaseState.HIRED}),
    CaseState.REJECTED: frozenset(),
    CaseState.NO_RESPONSE: frozenset(),
    CaseState.WITHDRAWN: frozenset(),
    CaseState.OUTCOME_REJECTED: frozenset(),
    CaseState.HIRED: frozenset(),
    CaseState.QUARANTINED: frozenset(),
    CaseState.CLOSED_OR_STALE: frozenset(),
}

TERMINAL_STATES: frozenset[CaseState] = frozenset(
    state for state, targets in ALLOWED_TRANSITIONS.items() if not targets
)

# States the workflow can still move on its own. "In flight" as the API
# reports it is this set plus the evidence-loop re-round (ADR-014).
IN_FLIGHT_STATES: frozenset[CaseState] = frozenset(
    {
        CaseState.DISCOVERED,
        CaseState.SNAPSHOTTED,
        CaseState.NORMALIZED,
        CaseState.DEDUPLICATED,
        CaseState.OFFICIAL_SOURCE_VERIFIED,
        CaseState.EVALUATED,
        CaseState.PENDING_APPROVAL,
    }
)

# The single source for "which decision moves a case where" — imported by
# both the workflow and the activity layer (both may import domain/).
DECISION_TO_STATE: dict[ApprovalDecision, CaseState] = {
    ApprovalDecision.APPROVE: CaseState.APPROVED,
    ApprovalDecision.REJECT: CaseState.REJECTED,
    ApprovalDecision.REQUEST_EVIDENCE: CaseState.NEEDS_MORE_EVIDENCE,
}


def can_transition(current: CaseState, requested: CaseState) -> bool:
    """Return whether the state machine permits `current -> requested`."""

    return requested in ALLOWED_TRANSITIONS[current]


def assert_transition(current: CaseState, requested: CaseState) -> None:
    """Raise TransitionError unless `current -> requested` is permitted."""

    if not can_transition(current, requested):
        raise TransitionError(current, requested)


_PIPELINE_RANK: dict[CaseState, int] = {
    CaseState.DISCOVERED: 0,
    CaseState.SNAPSHOTTED: 1,
    CaseState.NORMALIZED: 2,
    CaseState.DEDUPLICATED: 3,
    CaseState.OFFICIAL_SOURCE_VERIFIED: 4,
    CaseState.EVALUATED: 5,
    CaseState.PENDING_APPROVAL: 6,
    CaseState.APPROVED: 7,
    CaseState.REJECTED: 7,
    CaseState.NEEDS_MORE_EVIDENCE: 7,
    CaseState.SUBMITTED: 8,
    CaseState.INTERVIEWING: 9,
    CaseState.NO_RESPONSE: 9,
    CaseState.WITHDRAWN: 9,
    CaseState.OFFERED: 10,
    CaseState.OUTCOME_REJECTED: 10,
    CaseState.HIRED: 11,
    CaseState.QUARANTINED: 99,
    CaseState.CLOSED_OR_STALE: 99,
}


def is_retry_noop(current: CaseState, requested: CaseState) -> bool:
    """Whether re-applying `requested` on `current` is a converged retry.

    A retried Activity may re-apply a transition the case already made and
    moved past. When the requested state sits at or behind the case's
    pipeline depth — and is not otherwise a legal transition — the earlier
    delivery already happened and the retry must be a no-op (v4 §20).
    Callers check `can_transition` first, so genuine backward transitions
    (e.g. NEEDS_MORE_EVIDENCE -> OFFICIAL_SOURCE_VERIFIED) still apply.
    """

    if current == requested:
        return True
    return _PIPELINE_RANK[requested] <= _PIPELINE_RANK[current]


def is_terminal(state: CaseState) -> bool:
    """Return whether a case in this state accepts no further transitions."""

    return state in TERMINAL_STATES


def is_quarantinable(state: CaseState) -> bool:
    """Return whether operational failure may quarantine a case in this state."""

    return state in _QUARANTINABLE
