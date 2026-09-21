"""SQLAlchemy models for the system of record.

Column types are portable (String/JSON/DateTime/LargeBinary) so unit tests
can run on SQLite while deployments run on PostgreSQL.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all persistence models."""


class JobCaseRow(Base):
    __tablename__ = "job_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    company_hint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class BlobRow(Base):
    __tablename__ = "blobs"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceSnapshotRow(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("job_cases.id"), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    final_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    authority_level: Mapped[str] = mapped_column(String(40), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_object_key: Mapped[str] = mapped_column(String(128), nullable=False)
    extractor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    redirect_chain: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    request_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VerificationRow(Base):
    __tablename__ = "verification_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("job_cases.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    authority: Mapped[str] = mapped_column(String(40), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    apply_markers_found: Mapped[bool] = mapped_column(Boolean, nullable=False)
    closed_markers_found: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_changed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reading_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PageReadingRow(Base):
    """One bounded page reading: observations, verified claims, trail (ADR-012)."""

    __tablename__ = "page_readings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("job_cases.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stop_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationRunRow(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("job_cases.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_route: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalRequestRow(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("case_id", "generation", name="uq_approval_case_generation"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("job_cases.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    allowed_decisions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Artifacts under review, pinned at creation (v4 §10): the reviewer
    # decides against these ids, not against whatever is latest at render time.
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class HumanDecisionRow(Base):
    __tablename__ = "human_decisions"
    __table_args__ = (
        UniqueConstraint(
            "approval_request_id", "decision_version", name="uq_decision_request_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    approval_request_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("approval_requests.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # ADR-013: the reviewer signed for an unverified posting themselves.
    # The note is the evidence they saw; it is also the audit event's reason.
    attested_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attestation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Hash of the canonical request payload: a replayed key must present the
    # same fingerprint, otherwise the replay is a conflict, not a repeat.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
