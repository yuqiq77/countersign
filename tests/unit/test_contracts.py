"""Contract strictness tests that do not require infrastructure."""

import pytest
from pydantic import ValidationError

from countersign.contracts import ContractModel, HealthResponse, JobStatus


class ExampleContract(ContractModel):
    value: str


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ExampleContract(value="accepted", unexpected="rejected")  # type: ignore[call-arg]


def test_contracts_are_immutable() -> None:
    contract = ExampleContract(value="original")

    with pytest.raises(ValidationError):
        contract.value = "changed"  # type: ignore[misc]


def test_canonical_job_status_spelling() -> None:
    assert JobStatus.OPEN_THIRD_PARTY_ONLY.value == "OPEN_THIRD_PARTY_ONLY"


def test_health_contract_defaults() -> None:
    assert HealthResponse().model_dump() == {
        "status": "ok",
        "service": "countersign-api",
    }
