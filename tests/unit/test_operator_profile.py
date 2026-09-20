"""Operator profile loading: explicit path only, validated, never implicit."""

from pathlib import Path

import pytest

from countersign.gateway.evaluator import build_system_prompt, profile_fingerprint
from countersign.policy.operator_profile import (
    MAX_PROFILE_CHARS,
    PROFILE_PATH_VAR,
    load_operator_profile,
)


def test_unset_path_means_not_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROFILE_PATH_VAR, raising=False)
    assert load_operator_profile() is None
    monkeypatch.setenv(PROFILE_PATH_VAR, "  ")
    assert load_operator_profile() is None


def test_file_is_read_and_fingerprinted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / "profile.md"
    file.write_text("# Operator Profile\n\nFluent in two languages.\n\n", encoding="utf-8")
    monkeypatch.setenv(PROFILE_PATH_VAR, str(file))
    text = load_operator_profile()
    assert text == "# Operator Profile\n\nFluent in two languages."
    assert len(profile_fingerprint(text)) == 8
    assert profile_fingerprint(None) == "none"
    assert profile_fingerprint(text) != profile_fingerprint(text + " edited")


def test_set_path_must_name_a_usable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROFILE_PATH_VAR, str(tmp_path / "missing.md"))
    with pytest.raises(ValueError, match="not a file"):
        load_operator_profile()

    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv(PROFILE_PATH_VAR, str(empty))
    with pytest.raises(ValueError, match="empty"):
        load_operator_profile()

    huge = tmp_path / "huge.md"
    huge.write_text("x" * (MAX_PROFILE_CHARS + 1), encoding="utf-8")
    monkeypatch.setenv(PROFILE_PATH_VAR, str(huge))
    with pytest.raises(ValueError, match="limit"):
        load_operator_profile()


def test_system_prompt_states_presence_by_code_not_by_model() -> None:
    absent = build_system_prompt(None)
    assert absent.endswith("Candidate profile: NOT PROVIDED.")
    present = build_system_prompt("Engineer building data pipelines.")
    assert "--- CANDIDATE PROFILE (operator-provided) ---" in present
    assert "Engineer building data pipelines." in present
    assert "NOT PROVIDED" not in present.split("--- CANDIDATE PROFILE")[1]
    # The rubric text is the shared, cacheable prefix of both variants.
    assert present.startswith(absent.rsplit("\n\n", 1)[0])
