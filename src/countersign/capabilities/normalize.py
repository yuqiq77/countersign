"""Deterministic normalization: HTML to text facts, with cited markers.

Pure functions over fetched bytes — no I/O, no model calls. The extractor
is versioned so results are reproducible (v4 §12).
"""

import hashlib
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import urlparse

from countersign.contracts.enums import SourceAuthority

EXTRACTOR_NAME = "html-text"
EXTRACTOR_VERSION = "1.2.0"

_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})

# Fixed phrases are a stopgap, not a strategy: matching text anywhere in a
# page is blunt. Improvements go toward structural evidence — the actual
# apply button or link, JSON-LD `directApply` — never toward a longer list
# of fuzzy keywords. The last two are SmartRecruiters' apply-button label,
# with both apostrophes the sites use.
_APPLY_MARKERS = (
    "apply now",
    "apply for this job",
    "apply for this position",
    "submit application",
    "submit your application",
    "apply today",
    "jetzt bewerben",
    "i'm interested",
    "i\u2019m interested",
)

_CLOSED_MARKERS = (
    "no longer accepting applications",
    "no longer available",
    "position has been filled",
    "this job is closed",
    "job posting has expired",
    "this posting has expired",
    "diese stelle ist nicht mehr verf",
)

_ATS_HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "jobs.smartrecruiters.com",
)

_DISCOVERY_HOSTS = ("linkedin.com", "indeed.com", "glassdoor.com", "xing.com")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.title_parts: list[str] = []
        self.og_title: str | None = None
        self.site_name: str | None = None
        self.ldjson_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_ldjson = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "script":
            script_type = (dict(attrs).get("type") or "").strip().lower()
            self._in_ldjson = script_type == "application/ld+json"
        if tag == "meta":
            attributes = dict(attrs)
            content = (attributes.get("content") or "").strip()
            if content and attributes.get("property") == "og:site_name":
                self.site_name = content
            if content and attributes.get("property") == "og:title":
                self.og_title = content

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "script":
            self._in_ldjson = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._in_ldjson:
            self.ldjson_parts.append(data)
            return
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.chunks.append(stripped)


@dataclass(frozen=True)
class Normalized:
    """Deterministic text facts extracted from one snapshot."""

    title: str | None
    company_hint: str | None
    text: str
    content_hash: str
    apply_markers_found: bool
    closed_markers_found: bool
    # Where the text came from: "html" (visible text), "json-ld" (the
    # JobPosting description, used only when the visible text is empty), or
    # "none" (neither — the pipeline stops before the model on this).
    text_source: str


def _job_posting(parts: list[str]) -> dict[str, Any] | None:
    """The first JSON-LD object typed JobPosting, searching lists and @graph."""

    def walk(node: Any) -> dict[str, Any] | None:
        if isinstance(node, dict):
            typed = cast(dict[str, Any], node)
            kind: Any = typed.get("@type")
            kinds: list[Any] = cast(list[Any], kind) if isinstance(kind, list) else [kind]
            if "JobPosting" in kinds:
                return typed
            graph: Any = typed.get("@graph")
            return walk(graph) if graph is not None else None
        if isinstance(node, list):
            for item in cast(list[Any], node):
                found = walk(item)
                if found is not None:
                    return found
        return None

    for raw in parts:
        try:
            parsed: Any = json.loads(raw)
        except ValueError:
            continue
        found = walk(parsed)
        if found is not None:
            return found
    return None


def _visible_text(html: str) -> tuple[_TextExtractor, str]:
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor, "\n".join(extractor.chunks)


def normalize_html(body: bytes) -> Normalized:
    """Extract title, site hint, and posting text; hash the normalized text.

    The title prefers `og:title` over `<title>`: an ATS page has been
    observed serving a placeholder `<title>` (literally "page_title") on
    one response and the real one a second later, while `og:title` carried
    the posting title both times. The text hash never includes the title.

    Client-rendered ATS pages ship no visible text at all; the posting then
    lives only in a JSON-LD `JobPosting` block. That description is used
    as the text exactly when the visible text is empty — never merged with
    it, so pages that render both keep one deterministic source.
    """

    html = body.decode("utf-8", errors="replace")
    extractor, text = _visible_text(html)
    title = extractor.og_title or "".join(extractor.title_parts).strip() or None
    company_hint = extractor.site_name
    text_source = "html" if text else "none"
    if not text:
        posting = _job_posting(extractor.ldjson_parts)
        if posting is not None:
            description = posting.get("description")
            if isinstance(description, str):
                _, text = _visible_text(description)
                text_source = "json-ld" if text else "none"
            if title is None and isinstance(posting.get("title"), str):
                title = str(posting["title"]).strip() or None
            organization: Any = posting.get("hiringOrganization")
            if company_hint is None and isinstance(organization, dict):
                name: Any = cast(dict[str, Any], organization).get("name")
                if isinstance(name, str) and name.strip():
                    company_hint = name.strip()
    lowered = text.lower()
    return Normalized(
        title=title,
        company_hint=company_hint,
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        apply_markers_found=any(marker in lowered for marker in _APPLY_MARKERS),
        closed_markers_found=any(marker in lowered for marker in _CLOSED_MARKERS),
        text_source=text_source,
    )


def classify_authority(url: str) -> SourceAuthority:
    """Slice-1 heuristic authority classification by host and path.

    Conservative on purpose: unknown hosts are COMPANY_CONTROLLED, never
    OFFICIAL_*; aggregators are DISCOVERY_ONLY. A path that merely looks
    like a careers page ("/jobs/...") proves nothing about the host and
    must never elevate authority — OFFICIAL_* gates the apply-now framing.
    """

    host = (urlparse(url).hostname or "").lower()
    if any(host == ats or host.endswith("." + ats) for ats in _ATS_HOSTS):
        return SourceAuthority.OFFICIAL_ATS
    if any(host == d or host.endswith("." + d) for d in _DISCOVERY_HOSTS):
        return SourceAuthority.DISCOVERY_ONLY
    return SourceAuthority.COMPANY_CONTROLLED
