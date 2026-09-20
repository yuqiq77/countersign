"""Contract strictness tests that do not require infrastructure."""

import pytest
from pydantic import ValidationError

from countersign.contracts import ContractModel, HealthResponse, JobStatus
from countersign.contracts.workflow_io import NormalizeOutcome, VerifyOutcome


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


def test_activity_results_from_existing_workflow_histories_remain_readable() -> None:
    NormalizeOutcome.model_validate_json(
        '{"title":"Engineer","company_hint":null,"content_hash":"abc","extractor_version":"1.1.0"}'
    )
    result = VerifyOutcome.model_validate_json(
        '{"verification":{"case_id":"case-1","status":"OPEN",'
        '"authority":"OFFICIAL_ATS","checked_at":"2026-09-19T12:00:00Z",'
        '"http_status":200,"final_url":"https://jobs.lever.co/example/1",'
        '"reason":"apply markers on an official source",'
        '"apply_markers_found":true,"closed_markers_found":false}}'
    )
    # Unknown in old histories must preserve their original evaluation path.
    assert result.observed_text_chars is None


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
