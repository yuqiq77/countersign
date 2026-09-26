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


def test_attestation_must_state_observed_open_and_only_accompanies_approve() -> None:
    from countersign.contracts.cases import ApprovalDecisionRequest, Attestation

    with pytest.raises(ValidationError, match="observed_open"):
        Attestation.model_validate({"observed_open": False, "note": "form loaded"})
    with pytest.raises(ValidationError):  # bool stays strict at the JSON boundary
        Attestation.model_validate({"observed_open": "true", "note": "form loaded"})
    with pytest.raises(ValidationError):
        Attestation.model_validate({"observed_open": True, "note": "  "})
    with pytest.raises(ValidationError, match="only accompanies APPROVE"):
        ApprovalDecisionRequest.model_validate(
            {
                "decision": "REJECT",
                "reason": "no",
                "decision_version": 1,
                "attestation": {"observed_open": True, "note": "form loaded"},
            }
        )
    accepted = ApprovalDecisionRequest.model_validate(
        {
            "decision": "APPROVE",
            "reason": "ok",
            "decision_version": 1,
            "attestation": {"observed_open": True, "note": "form loaded"},
        }
    )
    assert accepted.attestation is not None and accepted.attestation.note == "form loaded"


def test_page_readings_stored_with_string_rejections_remain_readable() -> None:
    from countersign.contracts.page_reading import PageReadingRecord, RejectedClaim

    record = PageReadingRecord.model_validate(
        {
            "reading_id": "read-case-1-g1",
            "case_id": "case-1",
            "url": "https://jobs.lever.co/example/1",
            "observations": [],
            "body_observation": 0,
            "apply_entry": None,
            "evidence": [],
            "rejected_claims": [
                "apply_entry: 'Apply' or its paired target not found in observation 0"
            ],
            "steps": [],
            "stop_reason": "DONE",
            "prompt_version": "page-reader/0.4.0",
            "usage": None,
            "read_at": "2026-09-26T12:00:00Z",
        },
        strict=False,
    )
    (claim,) = record.rejected_claims
    assert claim.part == "" and claim.observation == -1
    assert claim.describe() == claim.reason
    assert claim.reason.startswith("apply_entry: 'Apply'")
    structured = RejectedClaim(
        part="evidence",
        kind="CLOSED_NOTICE",
        cited_text="filled",
        observation=1,
        href=None,
        reason="not found",
    )
    assert structured.describe() == "evidence CLOSED_NOTICE 'filled' (observation 1): not found"
