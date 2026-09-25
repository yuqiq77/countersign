"""One stable workflow id per case; rounds share it (v4 §20, ADR-014)."""

from countersign.domain.ids import approval_request_id, reading_id, verification_id
from countersign.runtime import workflow_id_for


def test_workflow_id_has_no_generation_segment() -> None:
    assert workflow_id_for("c1") == "job-case/c1"


def test_business_ids_carry_the_generation() -> None:
    assert approval_request_id("c1", 2) == "appr-c1-g2"
    assert verification_id("c1", 2) == "ver-c1-g2"
    assert reading_id("c1", 2) == "read-c1-g2"
