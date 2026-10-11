"""Every generated identifier must fit its column on PostgreSQL.

SQLite ignores VARCHAR lengths, so an oversized id passes every SQLite-backed
test and then fails on the deployment database. This test compares realistic
worst-case ids against the declared column lengths.
"""

import uuid

from countersign.contracts.enums import CaseState
from countersign.domain.ids import recheck_reading_id, recheck_verification_id
from countersign.persistence.models import (
    ApprovalRequestRow,
    AuditEventRow,
    BlobRow,
    EvaluationRunRow,
    HumanDecisionRow,
    PageReadingRow,
    SourceSnapshotRow,
    VerificationRow,
)

CASE_ID = str(uuid.uuid4())
LONGEST_STATE = max((s.value for s in CaseState), key=len)
DIGEST = "f" * 64
RUN_KEY = "f" * 8

SAMPLES = [
    (SourceSnapshotRow.__table__.c.id, f"snap-{CASE_ID[:8]}-{DIGEST[:16]}"),
    (VerificationRow.__table__.c.id, f"ver-{CASE_ID}-g999"),
    (PageReadingRow.__table__.c.id, f"read-{CASE_ID}-g999"),
    (VerificationRow.__table__.c.id, recheck_verification_id(CASE_ID, 999, RUN_KEY)),
    (PageReadingRow.__table__.c.id, recheck_reading_id(CASE_ID, 999, RUN_KEY)),
    (EvaluationRunRow.__table__.c.id, f"eval-{CASE_ID[:8]}-{DIGEST[:16]}"),
    (ApprovalRequestRow.__table__.c.id, f"appr-{CASE_ID}-g999"),
    (HumanDecisionRow.__table__.c.id, f"dec-appr-{CASE_ID}-g999-v999"),
    (AuditEventRow.__table__.c.id, f"aud-case-{CASE_ID}-v9999-{LONGEST_STATE}"),
    (AuditEventRow.__table__.c.id, f"aud-appr-expired-appr-{CASE_ID}-g999"),
    (AuditEventRow.__table__.c.id, f"aud-dec-appr-{CASE_ID}-g999-v999"),
    (AuditEventRow.__table__.c.id, f"aud-case-created-{CASE_ID}"),
    (AuditEventRow.__table__.c.id, f"aud-case-reappeared-{CASE_ID}"),
    (AuditEventRow.__table__.c.id, f"aud-watch-run-{RUN_KEY}"),
    (BlobRow.__table__.c.key, f"blob/sha256/{DIGEST}"),
]


def test_generated_ids_fit_their_columns() -> None:
    for column, sample in SAMPLES:
        length = getattr(column.type, "length", None)
        assert length is not None, f"{column} has no declared length"
        assert len(sample) <= length, (
            f"{column}: sample id {sample!r} is {len(sample)} chars, column allows {length}"
        )
