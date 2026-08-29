"""Engine and session management for the system of record."""

import os
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_DEFAULT_URL = "sqlite+pysqlite:///./countersign.db"

_engine: Engine | None = None
_engine_url: str | None = None


def database_url() -> str:
    """Resolve the database URL from the environment."""

    return os.environ.get("COUNTERSIGN_DATABASE_URL", _DEFAULT_URL)


def get_engine() -> Engine:
    """Return a process-wide engine, rebuilt if the URL changed (tests)."""

    global _engine, _engine_url
    url = database_url()
    if _engine is None or _engine_url != url:
        _engine = create_engine(url, future=True)
        _engine_url = url
    return _engine


def reset_engine() -> None:
    """Drop the cached engine; the next call rebuilds it (test isolation)."""

    global _engine, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None


@contextmanager
def session_scope() -> Generator[Session]:
    """One transactional unit of work: commit on success, rollback on error."""

    factory = sessionmaker(bind=get_engine(), future=True, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
