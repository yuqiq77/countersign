"""Deterministic compensation signal (v4 §14).

The model only extracts salary data; this module compares it against
operator-configured bands. The model never sees the bands or the
operator's own salary.
"""

from dataclasses import dataclass

from countersign.contracts.enums import CompensationSignal
from countersign.contracts.evaluation import SalaryExtraction


@dataclass(frozen=True)
class SalaryBands:
    """Annual gross thresholds in one currency; values are policy data."""

    currency: str
    transition_min: int
    acceptable_min: int
    attractive_min: int


def compute_compensation_signal(
    extraction: SalaryExtraction | None, bands: SalaryBands
) -> CompensationSignal:
    """Map extracted salary data onto the configured bands.

    Unknown, currency-mismatched, or amount-free extractions are UNKNOWN —
    the system never invents a salary judgment (anti-hallucination rule).
    """

    if extraction is None:
        return CompensationSignal.UNKNOWN
    if extraction.currency is None or extraction.currency.upper() != bands.currency:
        return CompensationSignal.UNKNOWN

    amounts = [a for a in (extraction.min_amount, extraction.max_amount) if a is not None]
    if not amounts:
        return CompensationSignal.UNKNOWN
    amount = max(amounts)

    if amount >= bands.attractive_min:
        return CompensationSignal.ATTRACTIVE
    if amount >= bands.acceptable_min:
        return CompensationSignal.ACCEPTABLE
    if amount >= bands.transition_min:
        return CompensationSignal.TRANSITION_DISCOUNT
    return CompensationSignal.CONCERNING
