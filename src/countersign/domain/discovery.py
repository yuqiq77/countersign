"""Pure rules of profile-driven discovery (ADR-017): turns, staleness, URL identity."""

import hashlib
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit

from countersign.contracts.enums import CandidateSource

# Query parameters that track a click rather than name a posting.
_TRACKING = frozenset(
    {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "gh_src", "lever-source"}
)


def other_source(source: CandidateSource) -> CandidateSource:
    return CandidateSource.SEARCH if source == CandidateSource.BOARD else CandidateSource.BOARD


def take_turns[T](
    first: CandidateSource, board: Sequence[T], search: Sequence[T]
) -> Iterator[tuple[CandidateSource, T]]:
    """One item from each source in turn, `first` going first.

    When one source runs out the other continues alone, so its share is
    never left unused; each source keeps its own order.
    """

    queues = {CandidateSource.BOARD: board, CandidateSource.SEARCH: search}
    order = (first, other_source(first))
    taken = dict.fromkeys(order, 0)
    while any(taken[source] < len(queues[source]) for source in order):
        for source in order:
            if taken[source] < len(queues[source]):
                yield source, queues[source][taken[source]]
                taken[source] += 1


class VerdictInputs(NamedTuple):
    """What a screen verdict was made from; it holds only while all are unchanged."""

    profile_fingerprint: str
    prompt_version: str
    route: str
    listing_hash: str


def verdict_is_stale(stored: VerdictInputs | None, current: VerdictInputs) -> bool:
    """Whether a stored verdict must be screened again before it counts."""

    return stored is None or stored != current


def listing_hash(title: str, published_at: datetime | None) -> str:
    """The listing facts a verdict read: the title, and a board posting's publication time."""

    stamp = ""
    if published_at is not None:
        aware = published_at.replace(tzinfo=UTC) if published_at.tzinfo is None else published_at
        stamp = aware.astimezone(UTC).isoformat()
    return hashlib.sha256(f"{title.strip().lower()}|{stamp}".encode()).hexdigest()[:16]


def _tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING


def url_key(url: str) -> str:
    """A posting's identity when no ATS adapter parses its URL.

    Lower-case scheme and host, no fragment, no trailing slash, tracking
    parameters removed; every other query parameter is kept, sorted, since
    careers sites often name the posting in one.
    """

    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port not in (None, 80, 443) else ""
    path = parts.path.rstrip("/") or "/"
    pairs = sorted(
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _tracking(name)
    )
    query = f"?{urlencode(pairs)}" if pairs else ""
    return f"url:{parts.scheme.lower()}://{host}{port}{path}{query}"


def _shape(url: str) -> tuple[str, str, str]:
    parts = urlsplit(url.strip())
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.path.rstrip("/") or "/"


def url_in_results(url: str, results: Iterable[str]) -> bool:
    """Whether a URL appeared in the search's own results (scheme, host and path)."""

    return _shape(url) in {_shape(result) for result in results}
