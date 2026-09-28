"""The operator's official-host mapping: private policy, loaded by path.

`COUNTERSIGN_OFFICIAL_HOSTS_PATH` names a JSON file shaped as
`contracts.authority_policy.OfficialHostMapping`; Compose mounts
`config/operator/` read-only and points at it. Unset means no mapping and
the classification that existed before it (ticket 002). There is
deliberately no implicit default path, and a set path that does not parse
stops the worker rather than classifying cases under a policy nobody can
name. The content fingerprint is the policy identity written with each
reading; hostnames themselves are never logged.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from pydantic import ValidationError

from countersign.capabilities.normalize import (
    classify_authority,
    host_of,
    is_shared_host,
    normalize_host,
)
from countersign.contracts.authority_policy import OfficialHostMapping
from countersign.contracts.enums import SourceAuthority
from countersign.env import optional_env

OFFICIAL_HOSTS_PATH_VAR = "COUNTERSIGN_OFFICIAL_HOSTS_PATH"
MAX_MAPPING_BYTES = 256_000


@dataclass(frozen=True)
class OfficialHostPolicy:
    """One loaded revision: declared version, content fingerprint, exact hosts.

    Loaded once per Activity attempt and used for every observation of that
    attempt; a later attempt may load a newer revision, and each reading
    records which one it used.
    """

    version: str
    fingerprint: str
    hosts: Mapping[str, str]

    def classify(self, url: str) -> SourceAuthority:
        return classify_authority(url, self.hosts)

    def entry_for(self, url: str) -> str | None:
        """The company key of the entry the URL's host matched, if any."""

        return self.hosts.get(host_of(url))


def parse_official_hosts(text: str, *, source: str = "<text>") -> OfficialHostPolicy:
    """Validate mapping text; every problem is a ValueError naming the source."""

    stripped = text.strip()
    if not stripped:
        raise ValueError(f"official hosts at {source!r}: empty")
    try:
        mapping = OfficialHostMapping.model_validate(json.loads(stripped))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"official hosts at {source!r}: {exc}") from exc
    hosts: dict[str, str] = {}
    for entry in mapping.entries:
        try:
            host = normalize_host(entry.host)
        except ValueError as exc:
            raise ValueError(f"official hosts at {source!r}: {exc}") from exc
        if is_shared_host(host):
            raise ValueError(
                f"official hosts at {source!r}: {host!r} is a shared application-system "
                "or discovery host, not a company host"
            )
        if host in hosts:
            raise ValueError(f"official hosts at {source!r}: {host!r} is listed twice")
        hosts[host] = entry.company
    return OfficialHostPolicy(
        version=mapping.version,
        # Same scheme as the profile: the stripped text, so a trailing newline
        # added by an editor is not a new policy revision.
        fingerprint=hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:8],
        hosts=MappingProxyType(hosts),
    )


def load_official_hosts(path: str | None = None) -> OfficialHostPolicy | None:
    """The mapping at `path`, else at the environment's path; None when unset.

    A set path must name a readable file within the size limit that parses
    and validates; anything else raises ValueError (the worker refuses to
    start on it). Callers that must not inherit the worker's environment,
    such as the replay diagnostic, pass the path explicitly.
    """

    location = path if path is not None else optional_env(OFFICIAL_HOSTS_PATH_VAR)
    if location is None:
        return None
    file = Path(location)
    try:
        if not file.is_file():
            raise ValueError(f"official hosts path {location!r} is not a file")
        size = file.stat().st_size
        if size > MAX_MAPPING_BYTES:
            raise ValueError(
                f"official hosts at {location!r} is {size} bytes; the limit is {MAX_MAPPING_BYTES}"
            )
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        # Unreadable is a configuration failure like malformed: the same
        # ValueError contract, so the Activity's PolicyError covers it.
        raise ValueError(f"official hosts at {location!r} cannot be read: {exc}") from exc
    return parse_official_hosts(text, source=location)
