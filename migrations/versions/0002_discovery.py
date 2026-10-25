"""Profile-driven discovery: candidates, search briefs, search requests (ADR-017).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_candidates",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("posting_key", sa.Text(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("origin", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("company", sa.String(256), nullable=True),
        sa.Column("locations", sa.JSON(), nullable=False),
        sa.Column("department", sa.String(256), nullable=True),
        sa.Column("team", sa.String(256), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("verdict_profile", sa.String(16), nullable=True),
        sa.Column("verdict_prompt_version", sa.String(64), nullable=True),
        sa.Column("verdict_route", sa.String(128), nullable=True),
        sa.Column("verdict_listing_hash", sa.String(16), nullable=True),
        sa.Column("screened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_passed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_open", sa.Boolean(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("job_cases.id"), nullable=True),
    )
    op.create_index(
        "ix_discovery_candidates_status", "discovery_candidates", ["status", "last_seen_at"]
    )
    op.create_table(
        "search_briefs",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("profile_fingerprint", sa.String(16), nullable=False),
        sa.Column("brief", sa.JSON(), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model_route", sa.String(128), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "discovery_searches",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_key", sa.String(16), nullable=False),
        sa.Column("request_index", sa.Integer(), nullable=False),
        sa.Column("queries", sa.JSON(), nullable=False),
        sa.Column("searches", sa.Integer(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("discovery_searches")
    op.drop_table("search_briefs")
    op.drop_index("ix_discovery_candidates_status", table_name="discovery_candidates")
    op.drop_table("discovery_candidates")
