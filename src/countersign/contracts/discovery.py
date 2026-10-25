"""Contracts of profile-driven discovery (ADR-017, ticket 005).

Model outputs keep every field required: no optional objects at decision
points. The brief is what the search may know of the profile, so it holds
short typed entries and no free text.
"""

from datetime import datetime
from typing import Annotated

from pydantic import Field

from countersign.contracts.base import ContractModel
from countersign.contracts.enums import CandidateSource, CandidateStatus, ScreenDecision

_Entry = Annotated[str, Field(min_length=1, max_length=60)]
_Entries = Annotated[list[_Entry], Field(max_length=12)]


class SearchBrief(ContractModel):
    """The search's only view of the profile: short entries, no free text."""

    role_families: _Entries
    skills: _Entries
    regions: _Entries
    languages: _Entries
    exclude_roles: _Entries


class SearchCandidate(ContractModel):
    """One posting the search proposes, from its own results."""

    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    company: str = Field(max_length=256)
    location: str = Field(max_length=256)
    reason: str = Field(max_length=500)


class SearchCandidates(ContractModel):
    """The search's final answer."""

    candidates: list[SearchCandidate] = Field(max_length=25)


class ScreenVerdict(ContractModel):
    """The coarse screen's answer for one candidate."""

    verdict: ScreenDecision
    reason: str = Field(min_length=1, max_length=300)


class CandidateView(ContractModel):
    """One recorded candidate as the lists show it."""

    # Stable per candidate: the UI keys its "Start case" request by it.
    id: str
    key: str
    url: str
    source: CandidateSource
    origin: str
    title: str
    company: str | None
    status: CandidateStatus
    reason: str | None
    first_seen_at: datetime
    last_seen_at: datetime


class BriefView(ContractModel):
    """The brief derived for the current profile, as the operator sees it."""

    profile_fingerprint: str
    brief: SearchBrief
    prompt_version: str
    model_route: str
    created_at: datetime
