"""Operator salary bands from the environment: blank is unset, all or nothing."""

import pytest

from countersign.policy.defaults import EXAMPLE_BANDS, SALARY_BAND_VARS, salary_bands

_FULL = {
    "COUNTERSIGN_SALARY_CURRENCY": " eur ",
    "COUNTERSIGN_SALARY_TRANSITION_MIN": "50000",
    "COUNTERSIGN_SALARY_ACCEPTABLE_MIN": "70000",
    "COUNTERSIGN_SALARY_ATTRACTIVE_MIN": "95000",
}


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SALARY_BAND_VARS:
        monkeypatch.delenv(name, raising=False)


def test_unset_and_blank_both_fall_back_to_the_example(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    assert salary_bands() is EXAMPLE_BANDS
    for name in SALARY_BAND_VARS:
        monkeypatch.setenv(name, "  ")
    assert salary_bands() is EXAMPLE_BANDS


def test_full_configuration_is_parsed_and_currency_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in _FULL.items():
        monkeypatch.setenv(name, value)
    bands = salary_bands()
    assert bands.currency == "EUR"
    assert (bands.transition_min, bands.acceptable_min, bands.attractive_min) == (
        50000,
        70000,
        95000,
    )


def test_partial_configuration_refuses_instead_of_mixing_currencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Currency alone used to pair the operator's currency with example amounts."""

    _clear(monkeypatch)
    monkeypatch.setenv("COUNTERSIGN_SALARY_CURRENCY", "EUR")
    with pytest.raises(ValueError, match="COUNTERSIGN_SALARY_TRANSITION_MIN"):
        salary_bands()


def test_blank_amount_counts_as_unset_not_as_int_of_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in _FULL.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("COUNTERSIGN_SALARY_ATTRACTIVE_MIN", "")
    with pytest.raises(ValueError, match="unset: COUNTERSIGN_SALARY_ATTRACTIVE_MIN"):
        salary_bands()


def test_non_integer_and_misordered_amounts_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in _FULL.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("COUNTERSIGN_SALARY_ACCEPTABLE_MIN", "70k")
    with pytest.raises(ValueError, match="integer amount"):
        salary_bands()
    monkeypatch.setenv("COUNTERSIGN_SALARY_ACCEPTABLE_MIN", "99000")
    with pytest.raises(ValueError, match="ascend"):
        salary_bands()
