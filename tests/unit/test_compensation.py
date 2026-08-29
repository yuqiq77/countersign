"""Deterministic compensation signal over configured bands."""

from countersign.contracts.enums import CompensationSignal, SourceAuthority
from countersign.contracts.evaluation import SalaryExtraction
from countersign.domain.compensation import SalaryBands, compute_compensation_signal

BANDS = SalaryBands(
    currency="USD", transition_min=60_000, acceptable_min=90_000, attractive_min=120_000
)


def extraction(**overrides: object) -> SalaryExtraction:
    payload: dict[str, object] = {
        "official": True,
        "currency": "USD",
        "min_amount": 100_000,
        "max_amount": None,
        "source_authority": SourceAuthority.OFFICIAL_COMPANY,
        "cited_text": "100k",
    }
    payload.update(overrides)
    return SalaryExtraction.model_validate(payload)


def test_missing_extraction_is_unknown() -> None:
    assert compute_compensation_signal(None, BANDS) == CompensationSignal.UNKNOWN


def test_currency_mismatch_is_unknown() -> None:
    result = compute_compensation_signal(extraction(currency="EUR"), BANDS)
    assert result == CompensationSignal.UNKNOWN


def test_no_amounts_is_unknown() -> None:
    result = compute_compensation_signal(
        extraction(min_amount=None, max_amount=None), BANDS
    )
    assert result == CompensationSignal.UNKNOWN


def test_band_mapping() -> None:
    cases = {
        130_000: CompensationSignal.ATTRACTIVE,
        95_000: CompensationSignal.ACCEPTABLE,
        70_000: CompensationSignal.TRANSITION_DISCOUNT,
        40_000: CompensationSignal.CONCERNING,
    }
    for amount, expected in cases.items():
        assert (
            compute_compensation_signal(extraction(min_amount=amount), BANDS) == expected
        )


def test_uses_highest_stated_amount() -> None:
    result = compute_compensation_signal(
        extraction(min_amount=80_000, max_amount=125_000), BANDS
    )
    assert result == CompensationSignal.ATTRACTIVE
