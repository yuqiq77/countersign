"""Deterministic normalization: HTML to posting text and source facts.

Pure functions over fetched bytes — no I/O, no model calls. The extractor
is versioned so results are reproducible (v4 §12).
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import urlparse

from countersign.contracts.enums import SourceAuthority

EXTRACTOR_NAME = "html-text"
EXTRACTOR_VERSION = "1.2.0"

_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})

_ATS_HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "api.ashbyhq.com",
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
    # Where the text came from: "html" (visible text), "json-ld" (the
    # JobPosting description, used only when the visible text is empty), or
    # "none" (neither — the reader can still try navigation or rendering).
    text_source: str
    # The first JSON-LD `JobPosting` object on the page, when one parses;
    # the page reader's dossier summarises it from here.
    job_posting: dict[str, Any] | None = None


def extract_job_posting(parts: list[str]) -> dict[str, Any] | None:
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
    posting = extract_job_posting(extractor.ldjson_parts)
    if not text:
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
    return Normalized(
        title=title,
        company_hint=company_hint,
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text_source=text_source,
        job_posting=posting,
    )


_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^{_HOST_LABEL}(?:\.{_HOST_LABEL})*$")


def _idna(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"not a valid hostname: {host!r}") from exc


def host_of(url: str) -> str:
    """The URL's hostname as the authority rules compare it.

    Lower-case, no trailing dot, IDNA-encoded (the fetcher reports final
    URLs in punycode), so a configured host and an observed host meet in
    one form.
    """

    host = (urlparse(url).hostname or "").lower().rstrip(".")
    try:
        return _idna(host)
    except ValueError:
        return host


def normalize_host(value: str) -> str:
    """A configured hostname in the form `host_of` produces, or ValueError.

    Bare host only: labels of letters, digits and hyphens joined by dots,
    IDNA-encoded. No scheme, port, path, userinfo, whitespace or wildcard
    characters, so an entry can never be mistaken for a pattern.
    """

    host = _idna(value.strip().lower().rstrip("."))
    if not _HOSTNAME.match(host):
        raise ValueError(f"not a bare hostname: {value!r}")
    return host


def _under(host: str, hosts: tuple[str, ...]) -> bool:
    """Whether `host` is one of `hosts` or a subdomain of one."""

    return any(host == h or host.endswith("." + h) for h in hosts)


def is_shared_host(host: str) -> bool:
    """Known application-system vendor or discovery hosts: never a company entry."""

    return _under(host, (*_ATS_HOSTS, *_DISCOVERY_HOSTS))


def classify_authority(
    url: str, official_hosts: Mapping[str, str] | None = None
) -> SourceAuthority:
    """Authority classification by host (ADR-005).

    Conservative on purpose: known ATS hosts are OFFICIAL_ATS; a host the
    operator declared official is OFFICIAL_COMPANY on an exact match of
    the normalized hostname, with no wildcards, registrable-domain guesses
    or path rules (ticket 002); aggregators are DISCOVERY_ONLY; every other
    host is COMPANY_CONTROLLED, never OFFICIAL_*. A path that merely looks
    like a careers page ("/jobs/...") proves nothing about the host and
    must never elevate authority — OFFICIAL_* gates the apply-now framing.
    """

    host = host_of(url)
    if _under(host, _ATS_HOSTS):
        return SourceAuthority.OFFICIAL_ATS
    if official_hosts and host in official_hosts:
        return SourceAuthority.OFFICIAL_COMPANY
    if _under(host, _DISCOVERY_HOSTS):
        return SourceAuthority.DISCOVERY_ONLY
    return SourceAuthority.COMPANY_CONTROLLED
