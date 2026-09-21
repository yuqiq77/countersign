"""Contracts for the bounded page reader (ADR-012).

The model sees a dossier per observation and answers with one `PageStep`;
code executes the requested action, checks every cited span against the
stored observations, and decides the status. The persisted record keeps
the whole trail: which pages were observed, which claims survived the
citation check, which were rejected, and why the reading stopped.
"""

from datetime import datetime

from pydantic import Field, model_validator

from countersign.contracts.base import ContractModel
from countersign.contracts.enums import (
    ApplyEntryKind,
    EvidenceKind,
    ObservationHow,
    ReaderAction,
    ReaderStop,
    SourceAuthority,
)
from countersign.contracts.evaluation import ModelUsage


class PageControl(ContractModel):
    """A link or button's visible/accessibility label and observed target."""

    text: str
    href: str | None = None


class JsonLdSummary(ContractModel):
    """The status-relevant fields of a JSON-LD `JobPosting`, when present."""

    title: str | None = None
    organization: str | None = None
    date_posted: str | None = None
    valid_through: str | None = None
    direct_apply: bool | None = None


class PageDossier(ContractModel):
    """What the model is shown for one observation: facts, not the raw page."""

    observation: int
    source_url: str
    requested_url: str
    how: ObservationHow
    authority: SourceAuthority
    http_status: int
    text_chars: int
    # "html", "json-ld" (client-rendered page whose posting lives only in
    # structured data), or "none".
    text_source: str
    text_excerpt: str
    controls: list[PageControl] = Field(default_factory=lambda: [])
    # True when the page had more controls than the fixed cap; the list is
    # the first ones in document order, so a missing entry is not evidence
    # of absence.
    controls_truncated: bool = False
    jsonld: JsonLdSummary | None = None


class PageEvidence(ContractModel):
    """A claim about status, bound to a verbatim span of one observation."""

    kind: EvidenceKind
    cited_text: str = Field(min_length=1)
    observation: int
    href: str | None = None


class ApplyEntry(ContractModel):
    """Where and how one applies, bound to the observation that shows it."""

    kind: ApplyEntryKind
    cited_text: str = Field(min_length=1)
    observation: int
    href: str | None = None


class PageReading(ContractModel):
    """The model's proposed reading; every claim is verified by code before use."""

    body_observation: int
    apply_entry: ApplyEntry | None = None
    evidence: list[PageEvidence] = Field(default_factory=lambda: [])
    notes: str = ""


class PageStep(ContractModel):
    """One turn of the reader: an action plus the reading so far.

    `reading` is required on every step, not only on DONE: a nullable field
    was left null by a live model on DONE even after a repair prompt, so
    the schema itself now carries the reading and RENDER / FOLLOW steps
    give a provisional one (body_observation of what is known so far).
    """

    action: ReaderAction
    why: str = Field(min_length=1)
    reading: PageReading
    href: str | None = None

    @model_validator(mode="after")
    def _follow_has_href(self) -> "PageStep":
        if self.action == ReaderAction.FOLLOW and not self.href:
            raise ValueError("FOLLOW requires an href")
        return self


class ObservationRef(ContractModel):
    """A persisted observation the reading refers to."""

    index: int
    source_url: str
    how: ObservationHow
    http_status: int
    snapshot_id: str | None = None


class StepLog(ContractModel):
    """One executed step, for the audit trail."""

    step: int
    action: ReaderAction
    href: str | None = None
    outcome: str


class PageReadingRecord(ContractModel):
    """The persisted result of one bounded reading (model assessment truth)."""

    reading_id: str
    case_id: str
    url: str
    observations: list[ObservationRef]
    body_observation: int
    apply_entry: ApplyEntry | None
    evidence: list[PageEvidence]
    rejected_claims: list[str]
    steps: list[StepLog]
    stop_reason: ReaderStop
    prompt_version: str
    usage: ModelUsage | None
    read_at: datetime
