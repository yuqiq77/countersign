"""The replay diagnostic reads the stored body and shows the model's stated reasons."""

import uuid

import pytest

import countersign.activities.case_activities as case_activities
from countersign.capabilities.http_fetch import FetchResult
from countersign.contracts.enums import ApplyEntryKind, ReaderAction
from countersign.contracts.page_reading import ApplyEntry, PageReading, PageStep
from countersign.contracts.workflow_io import CaseWorkflowInput
from countersign.diagnostics.replay_reading import replay
from countersign.gateway.fake_provider import FakeEvaluationProvider

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
