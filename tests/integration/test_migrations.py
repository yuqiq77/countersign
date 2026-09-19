"""Alembic migration upgrade test against a fresh database."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES = {
    "job_cases",
    "blobs",
    "source_snapshots",
    "verification_records",
    "evaluation_runs",
    "approval_requests",
    "human_decisions",
    "audit_events",
    "idempotency_records",
}


def test_upgrade_head_creates_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = f"sqlite+pysqlite:///{tmp_path}/migrate.db"
    monkeypatch.setenv("COUNTERSIGN_DATABASE_URL", url)

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(config, "head")

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert EXPECTED_TABLES.issubset(tables)
