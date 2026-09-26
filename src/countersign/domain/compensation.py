"""Deterministic compensation signal (v4 §14).

The model only extracts salary data; this module compares it against
operator-configured bands. The model never sees the bands or the
operator's own salary.
"""

import hashlib
from dataclasses import dataclass

from countersign.contracts.enums import CompensationSignal, SalaryPeriod
from countersign.contracts.evaluation import SalaryExtraction

MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class SalaryBands:
    """Annual gross thresholds in one currency; values are policy data."""

    currency: str
    acceptable_min: int
    attractive_min: int

    def fingerprint(self) -> str:
        """Short stable hash of the currency and two thresholds (policy identity)."""

        material = "|".join(
            (
                self.currency,
                str(self.acceptable_min),
                str(self.attractive_min),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]

    def __post_init__(self) -> None:
        if not self.currency or self.currency != self.currency.strip().upper():
            raise ValueError(f"currency must be a non-blank upper-case code, got {self.currency!r}")
        if not 0 < self.acceptable_min < self.attractive_min:
            raise ValueError(
                "thresholds must be positive and ascend: "
                f"acceptable_min={self.acceptable_min} "
                f"< attractive_min={self.attractive_min}"
            )


def compute_compensation_signal(
    extraction: SalaryExtraction | None, bands: SalaryBands
) -> CompensationSignal:
    """Map extracted salary data onto the configured annual bands.

    Unknown, currency-mismatched, or amount-free extractions are UNKNOWN —
    the system never invents a salary judgment (anti-hallucination rule).
    A monthly figure is annualised by twelve (the conservative reading; a
    thirteenth salary would only raise it); an hourly or unstated period
    is UNKNOWN rather than guessed. A live posting stated a monthly figure
    that, compared as if annual, read CONCERNING.
    """

    if extraction is None:
        return CompensationSignal.UNKNOWN
    if extraction.currency is None or extraction.currency.upper() != bands.currency:
        return CompensationSignal.UNKNOWN
    if extraction.period not in (SalaryPeriod.ANNUAL, SalaryPeriod.MONTHLY):
        return CompensationSignal.UNKNOWN

    amounts = [a for a in (extraction.min_amount, extraction.max_amount) if a is not None]
    if not amounts:
        return CompensationSignal.UNKNOWN
    amount = max(amounts)
    if extraction.period == SalaryPeriod.MONTHLY:
        amount *= MONTHS_PER_YEAR

    if amount >= bands.attractive_min:
        return CompensationSignal.ATTRACTIVE
    if amount >= bands.acceptable_min:
        return CompensationSignal.ACCEPTABLE
    return CompensationSignal.CONCERNING
