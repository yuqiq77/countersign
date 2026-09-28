"""Contract for the operator's explicit official-host mapping (ticket 002).

The mapping is private policy loaded by path (`policy/official_hosts.py`);
this module only shapes it. Hosts are matched exactly after normalization,
each subdomain listed on its own; the company key is an audit label, not
an entity, relation or deduplication key.
"""

from pydantic import Field

from countersign.contracts.base import ContractModel


class OfficialHostEntry(ContractModel):
    """One hostname the operator declares official, labelled by company key."""

    host: str = Field(min_length=1, max_length=253)
    company: str = Field(min_length=1, max_length=128)


class OfficialHostMapping(ContractModel):
    """The mapping file's shape: a declared version and explicit host entries.

    `version` is the operator's own label for the revision; the identity
    recorded with each reading is the content fingerprint the loader
    computes. No wildcards, patterns or paths: an entry is one host, and a
    set file lists at least one — a mapping that maps nothing is a mistake
    to surface, not a configuration to run under.
    """

    version: str = Field(min_length=1, max_length=64)
    entries: list[OfficialHostEntry] = Field(min_length=1)
