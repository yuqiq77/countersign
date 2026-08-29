"""Canonical enums whose spelling must not drift across system boundaries."""

from enum import StrEnum


class JobStatus(StrEnum):
    """Observed application status after authority-aware verification."""

    OPEN = "OPEN"
    OPEN_THIRD_PARTY_ONLY = "OPEN_THIRD_PARTY_ONLY"
    UNKNOWN_STALE_RISK = "UNKNOWN_STALE_RISK"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class SourceAuthority(StrEnum):
    """Authority tier used to resolve conflicting source observations."""

    OFFICIAL_ATS = "OFFICIAL_ATS"
    OFFICIAL_COMPANY = "OFFICIAL_COMPANY"
    COMPANY_CONTROLLED = "COMPANY_CONTROLLED"
    TRUSTED_BOARD_REDIRECT = "TRUSTED_BOARD_REDIRECT"
    DISCOVERY_ONLY = "DISCOVERY_ONLY"


class InvocationSource(StrEnum):
    """Actors permitted by a capability definition."""

    WORKFLOW = "WORKFLOW"
    MODEL = "MODEL"
    HUMAN = "HUMAN"
