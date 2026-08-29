"""Pure domain logic: state machine, gates, and score arithmetic.

Imports `countersign.contracts` only — enforced by import-linter.
"""

from countersign.domain.compensation import SalaryBands, compute_compensation_signal
from countersign.domain.gates import apply_now_permitted, gated_decision
from countersign.domain.states import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    TransitionError,
    assert_transition,
    can_transition,
    is_quarantinable,
    is_retry_noop,
    is_terminal,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "SalaryBands",
    "TransitionError",
    "apply_now_permitted",
    "assert_transition",
    "can_transition",
    "compute_compensation_signal",
    "gated_decision",
    "is_quarantinable",
    "is_retry_noop",
    "is_terminal",
]
