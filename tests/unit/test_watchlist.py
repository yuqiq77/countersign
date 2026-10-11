"""Watchlist policy (ticket 004): loading, the structured filter, the Schedule
it describes, and the budget units its limits are counted in."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio.client import ScheduleActionStartWorkflow, ScheduleOverlapPolicy

from countersign.agents.page_reader import ReaderConfig
from countersign.domain.watch import NEW_CASE_MODEL_CALLS, RECHECK_MODEL_CALLS
from countersign.gateway.evaluator import EvaluatorConfig
from countersign.policy.watchlist import (
    WATCHLIST_PATH_VAR,
    load_watchlist,
    parse_watchlist,
    passes_filter,
)
from countersign.runtime import TASK_QUEUE, watch_schedule

EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "config" / "watchlist.example.json"
EXAMPLE_TEXT = EXAMPLE_PATH.read_text(encoding="utf-8")


def _variant(**changes: object) -> str:
    return json.dumps({**json.loads(EXAMPLE_TEXT), **changes})


def test_the_example_file_loads_with_a_content_fingerprint() -> None:
    policy = load_watchlist(str(EXAMPLE_PATH))
    assert policy is not None
    assert policy.watchlist.version == "example-1"
    assert len(policy.fingerprint) == 8 and int(policy.fingerprint, 16) >= 0
    assert parse_watchlist(EXAMPLE_TEXT + "\n").fingerprint == policy.fingerprint


def test_unset_or_blank_path_means_no_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WATCHLIST_PATH_VAR, raising=False)
    assert load_watchlist() is None
    monkeypatch.setenv(WATCHLIST_PATH_VAR, "   ")
    assert load_watchlist() is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        _variant(boards=[{"adapter": "lever", "board": "acme"}]),
        _variant(locations=[]),
        _variant(locations=[""]),
        _variant(exclude_titles=[""]),
        _variant(max_recheck_model_calls_per_run=-1),
    ],
)
def test_a_malformed_watchlist_fails_naming_its_source(text: str) -> None:
    with pytest.raises(ValueError, match="operator-watchlist"):
        parse_watchlist(text, source="operator-watchlist")


def test_a_missing_file_fails_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not a file"):
        load_watchlist(str(tmp_path / "absent.json"))


@pytest.mark.parametrize(
    ("title", "locations", "passes"),
    [
        ("Platform Engineer", ("New York", "Hybrid - TORONTO office"), True),
        ("Platform Engineer", ("Remote - United States",), False),
        ("Platform Engineer", (), True),
        ("Engineering Intern", ("Toronto",), False),
    ],
)
def test_the_filter_admits_allowed_locations_and_drops_excluded_titles(
    title: str, locations: tuple[str, ...], passes: bool
) -> None:
    watchlist = parse_watchlist(EXAMPLE_TEXT).watchlist
    assert passes_filter(watchlist, title, locations) is passes


def test_the_schedule_follows_the_watchlist() -> None:
    policy = parse_watchlist(EXAMPLE_TEXT)
    schedule = watch_schedule(policy)

    action = schedule.action
    assert isinstance(action, ScheduleActionStartWorkflow)
    assert (action.workflow, action.id, action.task_queue) == (
        "WatchWorkflow",
        "countersign-watch",
        TASK_QUEUE,
    )
    assert list(schedule.spec.cron_expressions) == ["0 7 * * 1,3,5"]
    assert schedule.spec.time_zone_name == "UTC"
    assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    assert schedule.policy.catchup_window == timedelta(days=1)
    assert schedule.state.paused is False
    assert schedule.state.note == f"watchlist example-1 ({policy.fingerprint})"


def test_the_budget_units_match_the_calls_they_bound() -> None:
    reader = ReaderConfig()
    assert RECHECK_MODEL_CALLS == reader.max_steps * (reader.max_repairs + 1)
    assert NEW_CASE_MODEL_CALLS == RECHECK_MODEL_CALLS + EvaluatorConfig().max_repair_attempts + 1
