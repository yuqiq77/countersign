"""Contract for the operator's watchlist (ticket 004, ADR-016, ADR-017).

The watchlist is private policy loaded by path (`policy/watchlist.py`);
this module only shapes it. Its structured filters are location texts a
posting must name and title substrings that drop it; relevance is judged
by the coarse screen (ADR-017) and the evaluator.
"""

from typing import Annotated, Literal

from pydantic import Field

from countersign.contracts.base import ContractModel

# An empty substring matches every text: it would admit every posting as a
# location or drop every posting as a title exclusion.
_Substring = Annotated[str, Field(min_length=1)]


class WatchSchedule(ContractModel):
    """When the watch runs: cron expressions in one time zone."""

    cron: list[str] = Field(min_length=1)
    time_zone: str = Field(min_length=1, max_length=64)


class WatchBoard(ContractModel):
    """One ATS board read through its public API."""

    adapter: Literal["ashby", "greenhouse"]
    board: str = Field(min_length=1, max_length=128)


class Watchlist(ContractModel):
    """The file's shape. `version` is the operator's label; the identity
    recorded with each run is the content fingerprint the loader computes."""

    version: str = Field(min_length=1, max_length=64)
    schedule: WatchSchedule
    # May be empty when the search is on (`policy/watchlist.py` checks that
    # one source exists).
    boards: list[WatchBoard] = Field(default_factory=lambda: [])
    # Case-insensitive substrings of the posting's location texts; also the
    # search's regions (ADR-017).
    locations: list[_Substring] = Field(min_length=1)
    exclude_titles: list[_Substring] = Field(default_factory=lambda: [])
    published_within_days: int = Field(default=14, ge=1)
    # New cases per run, board and search together (ADR-017 decision 7).
    max_new_cases_per_run: int = Field(ge=0)
    max_recheck_model_calls_per_run: int = Field(ge=0)
    # Searches per run across every request of the search step; 0 turns the
    # search off.
    max_searches_per_run: int = Field(ge=0)
    # One budget for the brief, the search's requests and both screens.
    max_discovery_model_calls_per_run: int = Field(ge=0)
