"""The replay diagnostic reads the stored body and shows the model's stated reasons."""

import uuid
from pathlib import Path

import pytest

import countersign.activities.case_activities as case_activities
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.enums import ApplyEntryKind, ReaderAction
from countersign.contracts.page_reading import ApplyEntry, PageReading, PageStep
from countersign.contracts.workflow_io import CaseWorkflowInput
from countersign.diagnostics.replay_reading import replay
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.policy.official_hosts import (
    OFFICIAL_HOSTS_PATH_VAR,
    OfficialHostPolicy,
    load_official_hosts,
)

URL = "https://boards.greenhouse.io/acme/jobs/1"
HTML = b'<h1>Engineer</h1><p>Build things.</p><a href="/acme/jobs/1/apply">Apply for this Job</a>'


@pytest.mark.usefixtures("db")
def test_replay_uses_the_stored_body_and_reports_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def fake_fetch(url: str, **_: object) -> FetchResult:
        fetched.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            content_type="text/html",
            body=HTML,
            redirect_chain=[],
        )

    monkeypatch.setattr(case_activities, "fetch_url", fake_fetch)
    case_id = str(uuid.uuid4())
    case_activities.snapshot_source(CaseWorkflowInput(case_id=case_id, url=URL))
    assert fetched == [URL]

    provider = FakeEvaluationProvider(
        page_steps=[
            PageStep(
                action=ReaderAction.RENDER,
                why="check scripts",
                reading=PageReading(
                    body_observation=0, apply_entry=ApplyEntry(kind=ApplyEntryKind.NONE)
                ),
            ),
            PageStep(
                action=ReaderAction.DONE,
                why="apply control on the page",
                reading=PageReading(
                    body_observation=0,
                    apply_entry=ApplyEntry(
                        kind=ApplyEntryKind.APPLY_CONTROL,
                        observation=0,
                        cited_text="Apply for this Job",
                        href="https://boards.greenhouse.io/acme/jobs/1/apply",
                    ),
                    notes="single posting page",
                ),
            ),
        ]
    )
    out = replay(case_id, provider=provider)
    assert fetched == [URL]  # the replay never refetched
    assert out["observations"][0]["controls"] == 1
    assert [(s["action"], s["why"], s["outcome"]) for s in out["steps"]] == [
        ("RENDER", "check scripts", "render unavailable"),
        ("DONE", "apply control on the page", "reading proposed"),
    ]
    assert out["apply_entry"] is not None and out["apply_entry"]["kind"] == "APPLY_CONTROL"
    assert out["notes"] == "single posting page"
    assert out["classification"]["status"] == "OPEN"
    with pytest.raises(SystemExit):
        replay("no-such-reference", provider=provider)


COMPANY_URL = "https://careers.acme.example/roles/1"
COMPANY_HTML = (
    b'<h1>Platform Engineer</h1><p>Build the platform.</p><a href="/roles/1/apply">Apply now</a>'
)


def _company_provider() -> FakeEvaluationProvider:
    return FakeEvaluationProvider(
        page_steps=[
            PageStep(
                action=ReaderAction.DONE,
                why="apply control on the company page",
                reading=PageReading(
                    body_observation=0,
                    apply_entry=ApplyEntry(
                        kind=ApplyEntryKind.APPLY_CONTROL,
                        observation=0,
                        cited_text="Apply now",
                        href="https://careers.acme.example/roles/1/apply",
                    ),
                ),
            )
        ]
    )


@pytest.mark.usefixtures("db")
def test_replay_classifies_with_an_explicit_policy_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ticket 002: no mapping unless `--policy` names one, whatever the
    worker's environment says; the output states which one it used."""

    def fake_fetch(url: str, **_: object) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            content_type="text/html",
            body=COMPANY_HTML,
            redirect_chain=[],
        )

    monkeypatch.setattr(case_activities, "fetch_url", fake_fetch)
    monkeypatch.delenv(OFFICIAL_HOSTS_PATH_VAR, raising=False)
    case_id = str(uuid.uuid4())
    case_activities.snapshot_source(CaseWorkflowInput(case_id=case_id, url=COMPANY_URL))

    mapping = tmp_path / "hosts.json"
    mapping.write_text(
        '{"version": "replay-1", "entries": [{"host": "careers.acme.example", "company": "acme"}]}'
    )
    # The worker's environment names a mapping; the replay must not pick it up.
    monkeypatch.setenv(OFFICIAL_HOSTS_PATH_VAR, str(mapping))

    plain = replay(case_id, provider=_company_provider())
    assert plain["authority_policy"] is None
    assert plain["observations"][0]["authority"] == "COMPANY_CONTROLLED"
    assert plain["classification"]["status"] == "OPEN_THIRD_PARTY_ONLY"

    policy = load_official_hosts(str(mapping))
    mapped = replay(case_id, provider=_company_provider(), policy=policy)
    assert policy is not None
    assert mapped["authority_policy"] == {
        "version": "replay-1",
        "fingerprint": policy.fingerprint,
    }
    assert mapped["observations"][0]["authority"] == "OFFICIAL_COMPANY"
    assert mapped["classification"]["status"] == "OPEN"


def test_main_passes_the_policy_path_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import countersign.diagnostics.replay_reading as module

    mapping = tmp_path / "hosts.json"
    mapping.write_text(
        '{"version": "cli-1", "entries": [{"host": "careers.acme.example", "company": "acme"}]}'
    )
    seen: dict[str, object] = {}

    def fake_replay(reference: str, **kwargs: object) -> dict[str, object]:
        seen.update(reference=reference, **kwargs)
        return {"ok": True}

    monkeypatch.setattr(module, "replay", fake_replay)
    assert module.main(["snap-1", "--policy", str(mapping)]) == 0
    policy = seen["policy"]
    assert isinstance(policy, OfficialHostPolicy) and policy.version == "cli-1"
    assert seen["navigate"] is False
    assert module.main(["snap-1"]) == 0
    assert seen["policy"] is None
    assert '"ok": true' in capsys.readouterr().out
