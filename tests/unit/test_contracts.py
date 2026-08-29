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


class NumericContract(ContractModel):
    count: int


def test_contracts_are_strict_about_coercion() -> None:
    """The docstring says strict — so the string "1" is not an int."""

    with pytest.raises(ValidationError):
        NumericContract(count="1")  # type: ignore[arg-type]


def test_json_boundary_models_keep_numeric_strictness() -> None:
    from countersign.contracts.cases import ApprovalDecisionRequest

    with pytest.raises(ValidationError):
        ApprovalDecisionRequest.model_validate(
            {"decision": "APPROVE", "reason": "ok", "decision_version": "1"}
        )
    accepted = ApprovalDecisionRequest.model_validate(
        {"decision": "APPROVE", "reason": "ok", "decision_version": 1}
    )
    assert accepted.decision_version == 1
