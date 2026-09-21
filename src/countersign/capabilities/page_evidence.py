"""Page facts for the reader. Extraction preserves controls; the model judges their purpose."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import urljoin

from countersign.capabilities.normalize import normalize_html
from countersign.contracts.enums import ObservationHow, SourceAuthority
from countersign.contracts.page_reading import JsonLdSummary, PageControl, PageDossier

_EXCERPT_CHARS = 6_000
# Link-heavy pages (guest views of large boards) carry 150+ controls; past
# this cap the dossier says it was cut instead of silently dropping the rest.
MAX_CONTROLS = 120


class _ControlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[tuple[str, str | None]] = []
        self._current: list[str] | None = None
        self._href: str | None = None
        self._label = ""
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "input" and (attributes.get("type") or "").lower() in ("submit", "button"):
            label = attributes.get("aria-label") or attributes.get("value") or ""
            self.controls.append((label, None))
        if tag in ("a", "button"):
            self._current = []
            self._href = attributes.get("href")
            self._label = attributes.get("aria-label") or attributes.get("title") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
            return
        if tag in ("a", "button") and self._current is not None:
            label = self._label or " ".join(self._current)
            self.controls.append((" ".join(label.split()), self._href))
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._skip == 0 and self._current is not None:
            self._current.append(data)


def _jsonld_summary(posting: dict[str, Any] | None) -> JsonLdSummary | None:
    if posting is None:
        return None
    organization: Any = posting.get("hiringOrganization")
    org_name: Any = (
        cast(dict[str, Any], organization).get("name") if isinstance(organization, dict) else None
    )
    direct: Any = posting.get("directApply")
    return JsonLdSummary(
        title=str(posting["title"]) if isinstance(posting.get("title"), str) else None,
        organization=str(org_name) if isinstance(org_name, str) else None,
        date_posted=str(posting["datePosted"])
        if isinstance(posting.get("datePosted"), str)
        else None,
        valid_through=str(posting["validThrough"])
        if isinstance(posting.get("validThrough"), str)
        else None,
        direct_apply=direct if isinstance(direct, bool) else None,
    )


@dataclass(frozen=True)
class Observation:
    """One response and its extracted facts, retaining the response's own provenance."""

    index: int
    source_url: str
    how: ObservationHow
    authority: SourceAuthority
    http_status: int
    body: bytes
    text: str
    text_source: str
    normalized_hash: str
    title: str | None
    company_hint: str | None
    controls: list[PageControl] = field(default_factory=lambda: [])
    controls_truncated: bool = False
    jsonld: JsonLdSummary | None = None
    requested_url: str = ""
    content_type: str = "text/html"
    redirect_chain: list[str] = field(default_factory=lambda: [])

    def dossier(self) -> PageDossier:
        return PageDossier(
            observation=self.index,
            source_url=self.source_url,
            requested_url=self.requested_url or self.source_url,
            how=self.how,
            authority=self.authority,
            http_status=self.http_status,
            text_chars=len(self.text),
            text_source=self.text_source,
            text_excerpt=self.text[:_EXCERPT_CHARS],
            controls=self.controls,
            controls_truncated=self.controls_truncated,
            jsonld=self.jsonld,
        )

    def citable_spans(self) -> list[str]:
        spans = [self.text, *(c.text for c in self.controls)]
        if self.jsonld is not None:
            spans.extend(v for v in (self.jsonld.title, self.jsonld.valid_through) if v)
            if self.jsonld.direct_apply is not None:
                spans.append(f"directApply: {str(self.jsonld.direct_apply).lower()}")
        return spans


def observe(
    *,
    index: int,
    source_url: str,
    how: ObservationHow,
    authority: SourceAuthority,
    http_status: int,
    body: bytes,
    requested_url: str = "",
    content_type: str = "text/html",
    redirect_chain: list[str] | None = None,
) -> Observation:
    normalized = normalize_html(body)
    parser = _ControlExtractor()
    parser.feed(body.decode("utf-8", errors="replace"))
    # Keep unlabeled links and controls after navigation menus as well. The
    # fetched body is size-limited; keyword filters would hide facts from the
    # model. One entry per target (the first non-empty label wins) or per
    # label for a target-less button, in document order, then a fixed cap.
    by_key: dict[tuple[str | None, str], PageControl] = {}
    for text, href in parser.controls:
        target = urljoin(source_url, href) if href else None
        key = (target, "" if target else text)
        current = by_key.get(key)
        if current is None or (not current.text and text):
            by_key[key] = PageControl(text=text, href=target)
    controls = list(by_key.values())
    truncated = len(controls) > MAX_CONTROLS
    return Observation(
        index=index,
        source_url=source_url,
        requested_url=requested_url or source_url,
        how=how,
        authority=authority,
        http_status=http_status,
        content_type=content_type,
        redirect_chain=redirect_chain or [],
        body=body,
        text=normalized.text,
        text_source=normalized.text_source,
        normalized_hash=normalized.content_hash,
        title=normalized.title,
        company_hint=normalized.company_hint,
        controls=controls[:MAX_CONTROLS],
        controls_truncated=truncated,
        jsonld=_jsonld_summary(normalized.job_posting),
    )
