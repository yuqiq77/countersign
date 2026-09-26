"""Deterministic compensation signal over configured bands."""

from countersign.contracts.enums import CompensationSignal, SalaryPeriod, SourceAuthority
from countersign.contracts.evaluation import SalaryExtraction
from countersign.domain.compensation import SalaryBands, compute_compensation_signal

BANDS = SalaryBands(currency="USD", acceptable_min=90_000, attractive_min=120_000)


def extraction(**overrides: object) -> SalaryExtraction:
    payload: dict[str, object] = {
        "official": True,
        "currency": "USD",
        "min_amount": 100_000,
        "max_amount": None,
        "source_authority": SourceAuthority.OFFICIAL_COMPANY,
        "cited_text": "100k",
        "period": SalaryPeriod.ANNUAL,
    }
    payload.update(overrides)
    return SalaryExtraction.model_validate(payload)


def test_missing_extraction_is_unknown() -> None:
    assert compute_compensation_signal(None, BANDS) == CompensationSignal.UNKNOWN


def test_currency_mismatch_is_unknown() -> None:
    result = compute_compensation_signal(extraction(currency="EUR"), BANDS)
    assert result == CompensationSignal.UNKNOWN


def test_no_amounts_is_unknown() -> None:
    result = compute_compensation_signal(extraction(min_amount=None, max_amount=None), BANDS)
    assert result == CompensationSignal.UNKNOWN


def test_band_mapping() -> None:
    cases = {
        130_000: CompensationSignal.ATTRACTIVE,
        120_000: CompensationSignal.ATTRACTIVE,
        119_999: CompensationSignal.ACCEPTABLE,
        95_000: CompensationSignal.ACCEPTABLE,
        90_000: CompensationSignal.ACCEPTABLE,
        89_999: CompensationSignal.CONCERNING,
        70_000: CompensationSignal.CONCERNING,
        40_000: CompensationSignal.CONCERNING,
    }
    for amount, expected in cases.items():
        assert compute_compensation_signal(extraction(min_amount=amount), BANDS) == expected


def test_uses_highest_stated_amount() -> None:
    result = compute_compensation_signal(extraction(min_amount=80_000, max_amount=125_000), BANDS)
    assert result == CompensationSignal.ATTRACTIVE


def test_bands_enforce_currency_case_and_threshold_order() -> None:
    import pytest

    with pytest.raises(ValueError, match="upper-case"):
        SalaryBands(currency="usd", acceptable_min=2, attractive_min=3)
    with pytest.raises(ValueError, match="ascend"):
        SalaryBands(currency="USD", acceptable_min=120_000, attractive_min=90_000)
    with pytest.raises(ValueError, match="ascend"):
        SalaryBands(currency="USD", acceptable_min=0, attractive_min=2)


def test_monthly_figures_are_annualised_and_other_periods_stay_unknown() -> None:
    """A live posting stated a monthly figure that, compared as if annual,
    read CONCERNING. Monthly is annualised by twelve; hourly or unstated
    periods are not guessed."""

    monthly = extraction(min_amount=9_000, period=SalaryPeriod.MONTHLY)  # 108k a year
    assert compute_compensation_signal(monthly, BANDS) == CompensationSignal.ACCEPTABLE
    high = extraction(min_amount=10_500, period=SalaryPeriod.MONTHLY)  # 126k a year
    assert compute_compensation_signal(high, BANDS) == CompensationSignal.ATTRACTIVE
    low = extraction(min_amount=7_000, period=SalaryPeriod.MONTHLY)  # 84k a year
    assert compute_compensation_signal(low, BANDS) == CompensationSignal.CONCERNING
    for period in (SalaryPeriod.HOURLY, SalaryPeriod.UNKNOWN):
        assert (
            compute_compensation_signal(extraction(min_amount=100_000, period=period), BANDS)
            == CompensationSignal.UNKNOWN
        ), period


def test_period_is_required_of_the_model_but_older_records_read_back() -> None:
    schema = SalaryExtraction.model_json_schema()
    assert "period" in schema["required"]
    legacy = SalaryExtraction.model_validate(
        {
            "official": True,
            "currency": "USD",
            "min_amount": 10_000,
            "max_amount": None,
            "source_authority": "OFFICIAL_ATS",
            "cited_text": "from USD 10000 - monthly",
        },
        strict=False,
    )
    assert legacy.period == SalaryPeriod.UNKNOWN
    assert compute_compensation_signal(legacy, BANDS) == CompensationSignal.UNKNOWN
