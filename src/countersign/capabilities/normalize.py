"""Deterministic normalization: HTML to text facts, with cited markers.

Pure functions over fetched bytes — no I/O, no model calls. The extractor
is versioned so results are reproducible (v4 §12).
"""

import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

from countersign.contracts.enums import SourceAuthority

EXTRACTOR_NAME = "html-text"
EXTRACTOR_VERSION = "1.1.0"

_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})

_APPLY_MARKERS = (
    "apply now",
    "apply for this job",
    "apply for this position",
    "submit application",
    "submit your application",
    "apply today",
    "jetzt bewerben",
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
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
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

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
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


def normalize_html(body: bytes) -> Normalized:
    """Extract title, site hint, and visible text; hash the normalized text.

    The title prefers `og:title` over `<title>`: an ATS page has been
    observed serving a placeholder `<title>` (literally "page_title") on
    one response and the real one a second later, while `og:title` carried
    the posting title both times. The text hash never includes the title.
    """

    html = body.decode("utf-8", errors="replace")
    extractor = _TextExtractor()
    extractor.feed(html)
    text = "\n".join(extractor.chunks)
    lowered = text.lower()
    title = extractor.og_title or "".join(extractor.title_parts).strip() or None
    return Normalized(
        title=title,
        company_hint=extractor.site_name,
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        apply_markers_found=any(marker in lowered for marker in _APPLY_MARKERS),
        closed_markers_found=any(marker in lowered for marker in _CLOSED_MARKERS),
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
