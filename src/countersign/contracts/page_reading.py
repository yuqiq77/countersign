"""Contracts for the bounded page reader (ADR-012).

The model sees a dossier per observation and answers with one `PageStep`;
code executes the requested action, checks every cited span against the
stored observations, and decides the status. The persisted record keeps
the whole trail: which pages were observed, which claims survived the
citation check, which were rejected, and why the reading stopped.
"""

from datetime import datetime
from typing import Any, cast

from pydantic import ConfigDict, Field, model_validator

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
    """The status-relevant fields of a JSON-LD `JobPosting`, when present.

    Serialized under the source's own names (`directApply`, `validThrough`,
    `datePosted`), so the dossier shows the pair the rules ask the model to
    cite. A live model quoted `direct_apply: true` from a dossier that used
    the field names and was rejected for it.
    """

    model_config = ConfigDict(serialize_by_alias=True)

    title: str | None = None
    organization: str | None = None
    date_posted: str | None = Field(default=None, serialization_alias="datePosted")
    valid_through: str | None = Field(default=None, serialization_alias="validThrough")
    direct_apply: bool | None = Field(default=None, serialization_alias="directApply")


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
    # structured data), "api" (an ATS adapter's posting text), or "none".
    text_source: str
    text_excerpt: str
    # True when the excerpt is the head and tail of a longer text with a
    # marked gap between them; both parts are verbatim and citable.
    text_truncated: bool = False
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
    """Where and how one applies, bound to the observation that shows it.

    Always present on a reading, with kind NONE when the observed pages
    show no entry. It used to be `ApplyEntry | None`; under constrained
    decoding the null branch was the path of least resistance, and live
    models across routes narrated a working Apply control in `notes` while
    leaving the field null. Making the choice explicit fixed that on the
    same page (replay of 2026-09-22). NONE carries no citation; code maps
    it to "no entry" after verification and never persists it.
    """

    kind: ApplyEntryKind
    observation: int = -1
    cited_text: str = ""
    href: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _none_carries_nothing(cls, data: Any) -> Any:
        if isinstance(data, dict):
            kind = cast(dict[str, Any], data).get("kind")
            if kind == ApplyEntryKind.NONE or kind == "NONE":
                normalized: dict[str, Any] = {"kind": ApplyEntryKind.NONE}
                return normalized
        return cast(Any, data)

    @model_validator(mode="after")
    def _cites_when_present(self) -> "ApplyEntry":
        if self.kind != ApplyEntryKind.NONE and (self.observation < 0 or not self.cited_text):
            raise ValueError("an apply entry cites an observation and its exact text")
        return self


class PageReading(ContractModel):
    """The model's proposed reading; every claim is verified by code before use."""

    body_observation: int
    apply_entry: ApplyEntry
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
    # The authority this attempt gave the observation and, when a configured
    # official host matched, that entry's company key (ticket 002). None on
    # records written before the fields existed: not recorded, not "none".
    authority: SourceAuthority | None = None
    official_host_entry: str | None = None


class StepLog(ContractModel):
    """One executed step, for the audit trail.

    `why` is the model's own stated reason for the step; empty for
    code-initiated steps and for records written before it was kept. A
    DONE step's outcome states how its reading verified, including the
    claims code rejected, so the model sees them when asked once more.
    """

    step: int
    action: ReaderAction
    href: str | None = None
    outcome: str
    why: str = ""


class RejectedClaim(ContractModel):
    """A claim code could not bind to an observation, kept as the model made it.

    `part` is "apply_entry" or "evidence"; `kind` is the claim's own kind.
    Records written before this model stored each rejection as one string;
    those load with the string as `reason` and no claim fields.
    """

    part: str = ""
    kind: str = ""
    cited_text: str = ""
    observation: int = -1
    href: str | None = None
    reason: str

    @model_validator(mode="before")
    @classmethod
    def _legacy_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            legacy: dict[str, Any] = {"reason": data}
            return legacy
        return data

    def describe(self) -> str:
        if not self.part:
            return self.reason
        target = f", href {self.href}" if self.href is not None else ""
        return (
            f"{self.part} {self.kind} {self.cited_text!r} "
            f"(observation {self.observation}{target}): {self.reason}"
        )


class PageReadingRecord(ContractModel):
    """The persisted result of one bounded reading (model assessment truth)."""

    reading_id: str
    case_id: str
    url: str
    observations: list[ObservationRef]
    body_observation: int
    apply_entry: ApplyEntry | None
    evidence: list[PageEvidence]
    rejected_claims: list[RejectedClaim]
    steps: list[StepLog]
    stop_reason: ReaderStop
    prompt_version: str
    usage: ModelUsage | None
    read_at: datetime
    # The model's free-text notes on the final reading: assessment, not
    # evidence, kept so an empty reading can be audited without a replay.
    notes: str = ""
    # The official-host mapping in force for this attempt (declared version
    # and content fingerprint) and the normalized text length of the body
    # observation, which a retried verify Activity returns with the stored
    # result. None means not recorded (older records), never "no mapping".
    authority_policy_version: str | None = None
    authority_policy_fingerprint: str | None = None
    body_text_chars: int | None = None
    # What the ATS adapter's attempt found (ticket 003), such as "ashby:
    # published" or "ashby: not published". None when no adapter applies to
    # the URL, and on records written before the field existed.
    adapter: str | None = None

    def body_ref(self) -> ObservationRef | None:
        """The observation reference the reading identified as the posting body."""

        return next((r for r in self.observations if r.index == self.body_observation), None)
