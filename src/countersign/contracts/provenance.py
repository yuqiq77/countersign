"""Provenance contracts: immutable observations of external sources (v4 §8.4)."""

from datetime import datetime

from countersign.contracts.base import ContractModel
from countersign.contracts.enums import SourceAuthority


class SnapshotRecord(ContractModel):
    """Minimum provenance record for one observation of one URL."""

    snapshot_id: str
    case_id: str
    canonical_url: str
    final_url: str
    source_type: str
    authority_level: SourceAuthority
    retrieved_at: datetime
    http_status: int
    content_type: str
    content_hash: str
    raw_object_key: str
    extractor_name: str
    extractor_version: str
    redirect_chain: list[str]
    request_trace_id: str | None = None
