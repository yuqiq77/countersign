"""Generic example policy values shipped with the public repository.

The operator's real bands and rubric weights are private policy data
(config/operator/, gitignored) and override these at deployment time.
"""

import os

from countersign.domain.compensation import SalaryBands

_EXAMPLE_BANDS = SalaryBands(
    currency="USD",
    transition_min=60_000,
    acceptable_min=90_000,
    attractive_min=120_000,
)


def salary_bands() -> SalaryBands:
    """Resolve salary bands: operator env overrides, else the generic example."""

    currency = os.environ.get("COUNTERSIGN_SALARY_CURRENCY")
    if currency is None:
        return _EXAMPLE_BANDS
    return SalaryBands(
        currency=currency,
        transition_min=int(os.environ.get("COUNTERSIGN_SALARY_TRANSITION_MIN", "60000")),
        acceptable_min=int(os.environ.get("COUNTERSIGN_SALARY_ACCEPTABLE_MIN", "90000")),
        attractive_min=int(os.environ.get("COUNTERSIGN_SALARY_ATTRACTIVE_MIN", "120000")),
    )
