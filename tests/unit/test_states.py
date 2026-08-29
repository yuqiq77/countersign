"""State machine transition rules."""

from itertools import pairwise

import pytest

from countersign.contracts.enums import CaseState
from countersign.domain.states import (
    ALLOWED_TRANSITIONS,
    TransitionError,
    assert_transition,
    can_transition,
    is_terminal,
)


def test_happy_path_chain_is_legal() -> None:
    chain = [
        CaseState.DISCOVERED,
        CaseState.SNAPSHOTTED,
        CaseState.NORMALIZED,
        CaseState.DEDUPLICATED,
        CaseState.OFFICIAL_SOURCE_VERIFIED,
        CaseState.EVALUATED,
        CaseState.PENDING_APPROVAL,
        CaseState.APPROVED,
        CaseState.SUBMITTED,
        CaseState.INTERVIEWING,
        CaseState.OFFERED,
        CaseState.HIRED,
    ]
    for current, requested in pairwise(chain):
        assert_transition(current, requested)


def test_skipping_verification_is_illegal() -> None:
    with pytest.raises(TransitionError):
        assert_transition(CaseState.DEDUPLICATED, CaseState.EVALUATED)


def test_terminal_states_accept_nothing() -> None:
    for state in (CaseState.HIRED, CaseState.REJECTED, CaseState.QUARANTINED):
        assert is_terminal(state)
        assert not any(can_transition(state, target) for target in CaseState)


def test_needs_more_evidence_reenters_verification() -> None:
    assert_transition(CaseState.PENDING_APPROVAL, CaseState.NEEDS_MORE_EVIDENCE)
    assert_transition(CaseState.NEEDS_MORE_EVIDENCE, CaseState.OFFICIAL_SOURCE_VERIFIED)


def test_every_pre_approval_state_can_quarantine() -> None:
    """Operational failure needs a landing path from every pipeline state.

    Notably OFFICIAL_SOURCE_VERIFIED: an evaluation contract failure occurs
    before EVALUATED is persisted, so quarantine must be legal from there.
    """

    for state in (
        CaseState.DISCOVERED,
        CaseState.SNAPSHOTTED,
        CaseState.NORMALIZED,
        CaseState.DEDUPLICATED,
        CaseState.OFFICIAL_SOURCE_VERIFIED,
        CaseState.EVALUATED,
        CaseState.NEEDS_MORE_EVIDENCE,
    ):
        assert can_transition(state, CaseState.QUARANTINED), state


def test_every_state_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(CaseState)


def test_retry_noop_for_already_passed_states() -> None:
    from countersign.domain.states import is_retry_noop

    # A retried Activity re-applies NORMALIZED after the case moved on.
    assert is_retry_noop(CaseState.DEDUPLICATED, CaseState.NORMALIZED)
    assert is_retry_noop(CaseState.APPROVED, CaseState.PENDING_APPROVAL)
    # Genuinely new transitions are not retry no-ops.
    assert not is_retry_noop(CaseState.PENDING_APPROVAL, CaseState.REJECTED)
    assert not is_retry_noop(CaseState.EVALUATED, CaseState.CLOSED_OR_STALE)
