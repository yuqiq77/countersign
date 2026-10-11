"""The operator's watchlist: private policy, loaded by path (ticket 004).

`COUNTERSIGN_WATCHLIST_PATH` names a JSON file shaped as
`contracts.watchlist.Watchlist`; Compose mounts `config/operator/`
read-only and points at it. Unset means no watch, and the worker pauses
the Schedule. A set path that does not parse stops the worker. The
content fingerprint is the identity recorded on every run; board names
are never logged.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from countersign.contracts.watchlist import Watchlist
from countersign.env import optional_env

WATCHLIST_PATH_VAR = "COUNTERSIGN_WATCHLIST_PATH"
MAX_WATCHLIST_BYTES = 64_000


@dataclass(frozen=True)
class WatchPolicy:
    """One loaded revision: the validated file and its content fingerprint."""

    watchlist: Watchlist
    fingerprint: str


def parse_watchlist(text: str, *, source: str = "<text>") -> WatchPolicy:
    """Validate watchlist text; every problem is a ValueError naming the source."""

    stripped = text.strip()
    try:
        watchlist = Watchlist.model_validate_json(stripped)
    except ValidationError as exc:
        raise ValueError(f"watchlist at {source!r}: {exc}") from exc
    return WatchPolicy(
        watchlist=watchlist,
        # The stripped text, as for the profile and the official hosts.
        fingerprint=hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:8],
    )


def load_watchlist(path: str | None = None) -> WatchPolicy | None:
    """The watchlist at `path`, else at the environment's path; None when unset.

    A set path must name a readable file within the size limit that
    validates; anything else raises ValueError.
    """

    location = path if path is not None else optional_env(WATCHLIST_PATH_VAR)
    if location is None:
        return None
    file = Path(location)
    try:
        if not file.is_file():
            raise ValueError(f"watchlist path {location!r} is not a file")
        size = file.stat().st_size
        if size > MAX_WATCHLIST_BYTES:
            raise ValueError(
                f"watchlist at {location!r} is {size} bytes; the limit is {MAX_WATCHLIST_BYTES}"
            )
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"watchlist at {location!r} cannot be read: {exc}") from exc
    return parse_watchlist(text, source=location)


def passes_filter(watchlist: Watchlist, title: str, locations: Sequence[str]) -> bool:
    """Whether a listed posting passes the structured filter.

    A title containing an excluded substring drops it. Otherwise it passes
    when one of its location texts names an allowed location, or when it
    has no location text at all: the filter may cost an evaluation, never
    drop a posting silently, and the run counts those. A remote flag is
    not a location text, so a remote posting passes only by naming an
    allowed region.
    """

    lowered = title.lower()
    if any(excluded.lower() in lowered for excluded in watchlist.exclude_titles):
        return False
    if not locations:
        return True
    return any(
        wanted.lower() in text.lower() for text in locations for wanted in watchlist.locations
    )
