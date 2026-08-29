"""BlobStore: content-addressed raw bodies behind a swappable interface.

PostgreSQL-backed today; object storage later (v4 §7). Provenance always
records `raw_object_key` + hash, so the backend swap never rewrites history.
Temporal payloads carry references, never raw bodies.
"""

import hashlib
from typing import Protocol

from sqlalchemy.orm import Session

from countersign.persistence.models import BlobRow
from countersign.persistence.repo import utcnow


def sha256_hex(content: bytes) -> str:
    """The digest shared by blob keys, blob rows, and snapshot hashes."""

    return hashlib.sha256(content).hexdigest()


def blob_key_for(digest: str) -> str:
    """Content-addressed key from a sha256 digest: identical bodies share one blob."""

    return f"blob/sha256/{digest}"


class BlobStore(Protocol):
    """Minimal interface every blob backend must provide."""

    def put(self, content: bytes, media_type: str, *, sha256: str | None = None) -> str:
        """Store content, returning its object key. Idempotent."""
        ...

    def get(self, key: str) -> bytes | None:
        """Return content for a key, or None when absent."""
        ...


class DatabaseBlobStore:
    """Blob storage in the system-of-record database (Slice 1 backend)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def put(self, content: bytes, media_type: str, *, sha256: str | None = None) -> str:
        digest = sha256 if sha256 is not None else sha256_hex(content)
        key = blob_key_for(digest)
        if self._session.get(BlobRow, key) is None:
            self._session.add(
                BlobRow(
                    key=key,
                    content=content,
                    media_type=media_type,
                    sha256=digest,
                    created_at=utcnow(),
                )
            )
        return key

    def get(self, key: str) -> bytes | None:
        row = self._session.get(BlobRow, key)
        return None if row is None else row.content
