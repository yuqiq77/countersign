"""Temporal Activities containing all non-deterministic I/O."""

from countersign.activities import case_activities
from countersign.activities.case_activities import (
    create_approval,
    evaluate_case,
    expire_approval,
    finalize_decision,
    mark_closed,
    mark_needs_evidence,
    normalize_case,
    quarantine_case,
    recheck_case,
    snapshot_source,
    verify_status,
)
from countersign.activities.watch_activities import WATCH_ACTIVITIES

ALL_ACTIVITIES = [*case_activities.ALL_ACTIVITIES, *WATCH_ACTIVITIES]

__all__ = [
    "ALL_ACTIVITIES",
    "create_approval",
    "evaluate_case",
    "expire_approval",
    "finalize_decision",
    "mark_closed",
    "mark_needs_evidence",
    "normalize_case",
    "quarantine_case",
    "recheck_case",
    "snapshot_source",
    "verify_status",
]
