"""Generic example policy values shipped with the public repository.

The operator's real bands are private policy data: they live in the
deployment environment (the gitignored `.env`, passed through Compose)
and override these.
"""

from countersign.domain.compensation import SalaryBands
from countersign.env import optional_env

EXAMPLE_BANDS = SalaryBands(
    currency="USD",
    transition_min=60_000,
    acceptable_min=90_000,
    attractive_min=120_000,
)

_CURRENCY_VAR = "COUNTERSIGN_SALARY_CURRENCY"
_AMOUNT_VARS = (
    "COUNTERSIGN_SALARY_TRANSITION_MIN",
    "COUNTERSIGN_SALARY_ACCEPTABLE_MIN",
    "COUNTERSIGN_SALARY_ATTRACTIVE_MIN",
)
SALARY_BAND_VARS = (_CURRENCY_VAR, *_AMOUNT_VARS)


def salary_bands() -> SalaryBands:
    """Operator salary bands from the environment, else the generic example.

    Blank counts as unset. All four unset: the example bands (the worker
    logs that at startup). Any one set: all four are required, amounts must
    be integers, thresholds must ascend, and the currency is normalized to
    upper case. A partial or malformed set raises ValueError (naming the
    variable) rather than silently pairing example amounts with the
    operator's currency; the worker refuses to start on it.
    """

    values = {name: optional_env(name) for name in SALARY_BAND_VARS}
    present = {name: value for name, value in values.items() if value is not None}
    if not present:
        return EXAMPLE_BANDS
    missing = [name for name in SALARY_BAND_VARS if name not in present]
    if missing:
        raise ValueError(
            "salary bands are partially configured (set all four or none); unset: "
            + ", ".join(missing)
        )
    amounts: dict[str, int] = {}
    for name in _AMOUNT_VARS:
        try:
            amounts[name] = int(present[name])
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer amount, got {present[name]!r}") from exc
    return SalaryBands(
        currency=present[_CURRENCY_VAR].upper(),
        transition_min=amounts[_AMOUNT_VARS[0]],
        acceptable_min=amounts[_AMOUNT_VARS[1]],
        attractive_min=amounts[_AMOUNT_VARS[2]],
    )
