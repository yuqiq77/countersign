"""Shared fixtures: an isolated SQLite database per test."""

from collections.abc import Generator
from pathlib import Path

import pytest

from countersign.persistence.db import get_engine, reset_engine
from countersign.persistence.models import Base


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    url = f"sqlite+pysqlite:///{tmp_path}/countersign-test.db"
    monkeypatch.setenv("COUNTERSIGN_DATABASE_URL", url)
    reset_engine()
    Base.metadata.create_all(get_engine())
    yield
    reset_engine()
