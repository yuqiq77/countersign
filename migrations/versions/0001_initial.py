"""Initial schema: cases, snapshots, blobs, verification, evaluation,
approvals, decisions, audit, idempotency.

Revision ID: 0001
Revises:
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("company_hint", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "blobs",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("job_cases.id"), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("final_url", sa.String(2048), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("authority_level", sa.String(40), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw_object_key", sa.String(128), nullable=False),
        sa.Column("extractor_name", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(32), nullable=False),
        sa.Column("redirect_chain", sa.JSON(), nullable=False),
        sa.Column("request_trace_id", sa.String(64), nullable=True),
    )
    op.create_table(
        "verification_records",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("job_cases.id"), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("authority", sa.String(40), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("final_url", sa.String(2048), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("apply_markers_found", sa.Boolean(), nullable=False),
        sa.Column("closed_markers_found", sa.Boolean(), nullable=False),
        sa.Column("observed_content_hash", sa.String(64), nullable=True),
        sa.Column("content_changed", sa.Boolean(), nullable=True),
        sa.Column("snapshot_id", sa.String(64), nullable=True),
        sa.Column("reading_id", sa.String(64), nullable=True),
    )
    op.create_table(
        "page_readings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("job_cases.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("stop_reason", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("job_cases.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model_route", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("job_cases.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("allowed_decisions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_id", sa.String(64), nullable=True),
        sa.Column("verification_id", sa.String(64), nullable=True),
        sa.Column("evaluation_id", sa.String(64), nullable=True),
        sa.UniqueConstraint("case_id", "generation", name="uq_approval_case_generation"),
    )
    op.create_table(
        "human_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "approval_request_id",
            sa.String(64),
            sa.ForeignKey("approval_requests.id"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("approved_without_official_open", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "approval_request_id", "decision_version", name="uq_decision_request_version"
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("workflow_id", sa.String(128), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("operation", sa.String(64), primary_key=True),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_table("audit_events")
    op.drop_table("human_decisions")
    op.drop_table("approval_requests")
    op.drop_table("evaluation_runs")
    op.drop_table("page_readings")
    op.drop_table("verification_records")
    op.drop_table("source_snapshots")
    op.drop_table("blobs")
    op.drop_table("job_cases")
