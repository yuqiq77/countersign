"""The bounded page reader: budgets, citation checks, allowed moves, and conservative failures."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from countersign.agents.page_reader import (
    ReaderConfig,
    ReaderResult,
    cites,
    read_page,
    reread_unchanged,
)
from countersign.capabilities.http_fetch import FetchPolicyError, FetchResult
from countersign.capabilities.normalize import classify_authority
from countersign.capabilities.page_evidence import Observation, observe
from countersign.contracts.base import ContractModel
from countersign.contracts.enums import (
    ApplyEntryKind,
    EvidenceKind,
    ObservationHow,
    ReaderAction,
    ReaderStop,
    SourceAuthority,
)
from countersign.contracts.page_reading import (
    ApplyEntry,
    ObservationRef,
    PageEvidence,
    PageReading,
    PageReadingRecord,
    PageStep,
)
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.gateway.provider import ParsedResponse

COMPANY_URL = "https://acme.example/careers/ai-engineer"
ATS_URL = "https://boards.greenhouse.io/acme/jobs/1"

COMPANY_HTML = b"""
<html><body>
  <h1>AI Engineer</h1>
  <p>We build agents. Read the full posting on our applicant portal.</p>
  <a href="https://boards.greenhouse.io/acme/jobs/1">See full posting</a>
  <a href="/privacy">Privacy</a>
</body></html>
"""
ATS_HTML = b"""
<html><body>
  <h1>AI Engineer</h1>
  <p>Build evaluation infrastructure for agents. Python required.</p>
  <a href="/acme/jobs/1/apply">Apply for this Job</a>
</body></html>
"""
SHELL_HTML = b'<html><head><script>app()</script></head><body><div id="root"></div></body></html>'
TEXT_ONLY_HTML = b"<html><body><h1>AI Engineer</h1><p>Static text, no controls.</p></body></html>"
JSONLD_ONLY_HTML = (
    b'<html><head><script type="application/ld+json">{"@type": "JobPosting", '
    b'"title": "AI Engineer", "description": "Posting text in structured data only."}'
    b'</script></head><body><div id="root"></div></body></html>'
)
RENDERED_HTML = (
    b"<html><body><h1>AI Engineer</h1><p>Rendered body.</p><button>Apply</button></body></html>"
)


_NONE = ApplyEntry(kind=ApplyEntryKind.NONE)
_PROVISIONAL = PageReading(body_observation=0, apply_entry=_NONE)
# Citation-rule tests script one DONE and read its rejections directly; the
# repair step, which would ask the fake once more, is covered on its own.
_STRICT = ReaderConfig(citation_repairs=0)


def _result(url: str, body: bytes, status: int = 200) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        http_status=status,
        content_type="text/html",
        body=body,
        redirect_chain=[],
    )


def _pages(**pages: bytes) -> Callable[[str, float], FetchResult]:
    def fetch(url: str, timeout: float) -> FetchResult:
        if url in pages:
            return _result(url, pages[url])
        raise httpx.ConnectError(f"no such page: {url}")

    return fetch


def _no_render(url: str, body: bytes, timeout: float) -> FetchResult | None:
    return None


def _run(
    provider: FakeEvaluationProvider,
    *,
    first_url: str = COMPANY_URL,
    first_html: bytes = COMPANY_HTML,
    fetch: Callable[[str, float], FetchResult] | None = None,
    render: Callable[[str, bytes, float], FetchResult | None] = _no_render,
    config: ReaderConfig | None = None,
) -> ReaderResult:
    return read_page(
        first_url,
        first=_result(first_url, first_html),
        authority_of=classify_authority,
        fetch=fetch or _pages(),
        render=render,
        provider=provider,
        config=config,
    )


def test_done_with_an_empty_reading_is_unassessed() -> None:
    result = _run(FakeEvaluationProvider())
    assert result.stop_reason == ReaderStop.DONE
    assert result.model_calls == 1
    assert result.apply_entry is None  # "See full posting" is not an apply control
    assert result.body_observation == 0


def test_follow_is_allowed_only_for_listed_hrefs_and_within_budget() -> None:
    steps = [
        PageStep(
            action=ReaderAction.FOLLOW,
            why="invented",
            href="https://evil.example/x",
            reading=_PROVISIONAL,
        ),
        PageStep(action=ReaderAction.FOLLOW, why="listed", href=ATS_URL, reading=_PROVISIONAL),
        PageStep(
            action=ReaderAction.DONE,
            why="apply entry on the ATS page",
            reading=PageReading(
                body_observation=1,
                apply_entry=ApplyEntry(
                    kind=ApplyEntryKind.APPLY_CONTROL,
                    cited_text="Apply for this Job",
                    observation=1,
                    href="https://boards.greenhouse.io/acme/jobs/1/apply",
                ),
            ),
        ),
    ]
    result = _run(
        FakeEvaluationProvider(page_steps=steps),
        fetch=_pages(**{ATS_URL: ATS_HTML}),
    )
    assert result.steps[0].outcome == "href not on page"
    assert result.steps[1].outcome.startswith("followed: http 200")
    assert len(result.observations) == 2
    assert result.observations[1].how == ObservationHow.FOLLOWED
    assert result.observations[1].authority == SourceAuthority.OFFICIAL_ATS
    assert result.body_observation == 1
    assert result.apply_entry is not None and result.apply_entry.observation == 1
    assert result.stop_reason == ReaderStop.DONE
    assert result.rejected_claims == []


def test_unverifiable_claims_are_rejected_not_believed() -> None:
    steps = [
        PageStep(
            action=ReaderAction.DONE,
            why="hallucinated",
            reading=PageReading(
                body_observation=0,
                apply_entry=ApplyEntry(
                    kind=ApplyEntryKind.APPLY_CONTROL, cited_text="Apply now", observation=0
                ),
                evidence=[
                    PageEvidence(
                        kind=EvidenceKind.CLOSED_NOTICE,
                        cited_text="position has been filled",
                        observation=0,
                    )
                ],
            ),
        )
    ]
    result = _run(FakeEvaluationProvider(page_steps=steps), config=_STRICT)
    assert result.apply_entry is None
    assert result.evidence == []
    assert len(result.rejected_claims) == 2
    assert all("not found in observation 0" in r.reason for r in result.rejected_claims)
    assert [(r.part, r.kind) for r in result.rejected_claims] == [
        ("apply_entry", "APPLY_CONTROL"),
        ("evidence", "CLOSED_NOTICE"),
    ]
    assert result.steps[-1].outcome.startswith("reading proposed; 2 claims rejected: ")


def test_render_unavailable_is_reported_and_budget_ends_the_reading() -> None:
    steps = [PageStep(action=ReaderAction.RENDER, why="shell page", reading=_PROVISIONAL)] * 3
    result = _run(
        FakeEvaluationProvider(page_steps=steps),
        first_html=SHELL_HTML,
        config=ReaderConfig(max_steps=3),
    )
    # Step 0 is the code-initiated pre-render of a shell page; the model's
    # own RENDER requests then find the URL already attempted.
    assert [(s.step, s.outcome) for s in result.steps] == [
        (0, "pre-render: render unavailable"),
        (1, "render already attempted"),
        (2, "render already attempted"),
        (3, "render already attempted"),
    ]
    assert result.stop_reason == ReaderStop.STEP_BUDGET
    assert result.apply_entry is None


def test_shell_page_is_rendered_before_the_first_model_step() -> None:
    def render(url: str, body: bytes, timeout: float) -> FetchResult | None:
        return _result(url, RENDERED_HTML)

    steps = [
        PageStep(
            action=ReaderAction.DONE,
            why="rendered page shows the button",
            reading=PageReading(
                body_observation=1,
                apply_entry=ApplyEntry(
                    kind=ApplyEntryKind.APPLY_CONTROL, cited_text="Apply", observation=1
                ),
            ),
        ),
    ]
    result = _run(FakeEvaluationProvider(page_steps=steps), first_html=SHELL_HTML, render=render)
    assert result.steps[0].step == 0
    assert result.steps[0].action == ReaderAction.RENDER
    assert result.steps[0].outcome.startswith("pre-render: rendered: http 200")
    assert result.observations[1].how == ObservationHow.RENDERED
    assert result.model_calls == 1
    assert result.apply_entry is not None and result.apply_entry.observation == 1
    assert result.stop_reason == ReaderStop.DONE


@pytest.mark.parametrize(
    ("html", "prerendered"),
    [
        (SHELL_HTML, True),
        (JSONLD_ONLY_HTML, True),
        (TEXT_ONLY_HTML, False),
        (COMPANY_HTML, False),
        (b'<div id="root"></div><a href="/apply">Apply</a>', False),
    ],
)
def test_prerender_only_when_static_extraction_yields_no_text_and_no_controls(
    html: bytes, prerendered: bool
) -> None:
    rendered: list[str] = []

    def render(url: str, body: bytes, timeout: float) -> FetchResult | None:
        rendered.append(url)
        return _result(url, RENDERED_HTML)

    result = _run(FakeEvaluationProvider(), first_html=html, render=render)
    assert (rendered == [COMPANY_URL]) is prerendered
    assert (result.steps[0].step == 0) is prerendered


def test_render_adds_an_observation_the_reading_can_cite() -> None:
    def render(url: str, body: bytes, timeout: float) -> FetchResult | None:
        return _result(url, RENDERED_HTML)

    steps = [
        PageStep(action=ReaderAction.RENDER, why="text but no controls", reading=_PROVISIONAL),
        PageStep(
            action=ReaderAction.DONE,
            why="rendered page shows the button",
            reading=PageReading(
                body_observation=1,
                apply_entry=ApplyEntry(
                    kind=ApplyEntryKind.APPLY_CONTROL, cited_text="Apply", observation=1
                ),
            ),
        ),
    ]
    result = _run(
        FakeEvaluationProvider(page_steps=steps), first_html=TEXT_ONLY_HTML, render=render
    )
    assert result.steps[0].step == 1  # no pre-render: the static page had text
    assert result.observations[1].how == ObservationHow.RENDERED
    assert result.apply_entry is not None and result.apply_entry.observation == 1
    assert result.body_observation == 1
    assert result.stop_reason == ReaderStop.DONE


def test_model_failure_keeps_body_without_inventing_an_entry() -> None:
    provider = FakeEvaluationProvider(fail_attempts=2)
    result = _run(
        provider, first_url=ATS_URL, first_html=ATS_HTML, config=ReaderConfig(max_repairs=1)
    )
    assert result.stop_reason == ReaderStop.MODEL_FAILURE
    assert result.apply_entry is None
    assert result.observations[result.body_observation].text
    assert result.model_calls == 2
    assert result.usage is None  # failed calls expose no token usage


def test_follow_fetch_failures_are_step_outcomes_not_errors() -> None:
    def fetch(url: str, timeout: float) -> FetchResult:
        raise FetchPolicyError("destination is not a public address")

    steps = [
        PageStep(action=ReaderAction.FOLLOW, why="listed", href=ATS_URL, reading=_PROVISIONAL),
        PageStep(action=ReaderAction.DONE, why="give up", reading=_PROVISIONAL),
    ]
    result = _run(FakeEvaluationProvider(page_steps=steps), fetch=fetch)
    assert result.steps[0].outcome.startswith("follow failed:")
    assert len(result.observations) == 1


def test_step_contract_requires_the_action_payload() -> None:
    with pytest.raises(ValueError, match="FOLLOW requires an href"):
        PageStep(action=ReaderAction.FOLLOW, why="no href", reading=_PROVISIONAL)
    with pytest.raises(ValueError):  # reading is required on every step
        PageStep.model_validate({"action": "DONE", "why": "no reading"})
    PageStep(action=ReaderAction.RENDER, why="shell", reading=_PROVISIONAL)


def test_apply_entry_is_a_required_explicit_choice() -> None:
    """The live failure mode: an optional entry object was left null while the
    model narrated the entry in notes. The reading now has to say NONE."""

    with pytest.raises(ValueError):  # no default: the model must choose
        PageReading.model_validate({"body_observation": 0})
    none = ApplyEntry.model_validate(
        {"kind": "NONE", "observation": 0, "cited_text": "Apply", "href": "https://x.example"}
    )
    assert (none.observation, none.cited_text, none.href) == (-1, "", None)
    with pytest.raises(ValueError, match="cites an observation"):
        ApplyEntry(kind=ApplyEntryKind.APPLY_CONTROL)
    with pytest.raises(ValueError, match="cites an observation"):
        ApplyEntry(kind=ApplyEntryKind.APPLY_CONTROL, observation=0)
    ApplyEntry(kind=ApplyEntryKind.APPLY_CONTROL, observation=0, cited_text="Apply")


def test_model_reasons_and_notes_are_kept_for_the_audit_trail() -> None:
    steps = [
        PageStep(
            action=ReaderAction.FOLLOW,
            why="board links the ATS",
            href=ATS_URL,
            reading=_PROVISIONAL,
        ),
        PageStep(
            action=ReaderAction.DONE,
            why="nothing to cite",
            reading=PageReading(body_observation=1, apply_entry=_NONE, notes="form needs a login"),
        ),
    ]
    result = _run(FakeEvaluationProvider(page_steps=steps), fetch=_pages(**{ATS_URL: ATS_HTML}))
    assert [s.why for s in result.steps] == ["board links the ATS", "nothing to cite"]
    assert result.notes == "form needs a login"
    assert result.apply_entry is None  # NONE never survives verification as an entry


def test_usage_accumulates_across_steps() -> None:
    steps = [
        PageStep(action=ReaderAction.FOLLOW, why="listed", href=ATS_URL, reading=_PROVISIONAL),
        PageStep(action=ReaderAction.DONE, why="done", reading=_PROVISIONAL),
    ]
    result = _run(FakeEvaluationProvider(page_steps=steps), fetch=_pages(**{ATS_URL: ATS_HTML}))
    assert result.model_calls == 2
    assert result.usage is not None and result.usage.output_tokens == 512


def test_cites_folds_whitespace_and_case_but_needs_a_real_span() -> None:
    obs = observe(
        index=0,
        source_url=ATS_URL,
        how=ObservationHow.STATIC,
        authority=SourceAuthority.OFFICIAL_ATS,
        http_status=200,
        body=ATS_HTML,
    )
    assert cites(obs, "apply for this   job")
    assert cites(obs, "Python required")
    assert not cites(obs, "Apply now")
    assert not cites(obs, "ok")


@pytest.mark.parametrize(
    "html",
    [
        b'<a href="mailto:friend@example.com">Share by email</a>',
        b"<button>Apply Later</button>",
    ],
)
def test_empty_reading_can_reject_misleading_controls(html: bytes) -> None:
    result = _run(FakeEvaluationProvider(), first_url=ATS_URL, first_html=html)
    assert result.apply_entry is None


def test_real_quote_does_not_validate_an_invented_target() -> None:
    reading = PageReading(
        body_observation=0,
        apply_entry=ApplyEntry(
            kind=ApplyEntryKind.APPLY_CONTROL,
            observation=0,
            cited_text="Privacy",
            href="https://invented.example/apply",
        ),
    )
    result = _run(
        FakeEvaluationProvider(
            page_steps=[PageStep(action=ReaderAction.DONE, why="done", reading=reading)]
        ),
        config=_STRICT,
    )
    assert result.apply_entry is None
    assert len(result.rejected_claims) == 1
    assert result.rejected_claims[0].href == "https://invented.example/apply"


def test_render_after_follow_uses_current_page_and_real_response_metadata() -> None:
    rendered_urls: list[str] = []

    def render(url: str, body: bytes, timeout: float) -> FetchResult:
        rendered_urls.append(url)
        return FetchResult(
            requested_url=url,
            final_url="https://unavailable.example/error",
            http_status=503,
            content_type="text/html",
            body=RENDERED_HTML,
            redirect_chain=[url],
        )

    result = _run(
        FakeEvaluationProvider(
            page_steps=[
                PageStep(
                    action=ReaderAction.FOLLOW, why="listed", href=ATS_URL, reading=_PROVISIONAL
                ),
                PageStep(action=ReaderAction.RENDER, why="shell", reading=_PROVISIONAL),
                PageStep(action=ReaderAction.DONE, why="unavailable", reading=_PROVISIONAL),
            ]
        ),
        fetch=_pages(**{ATS_URL: SHELL_HTML}),
        render=render,
    )
    assert rendered_urls == [ATS_URL]
    assert result.observations[-1].http_status == 503
    assert result.observations[-1].authority == SourceAuthority.COMPANY_CONTROLLED


def test_late_model_result_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    import countersign.agents.page_reader as reader

    clock = [0.0]
    monkeypatch.setattr(reader.time, "monotonic", lambda: clock[0])
    provider = FakeEvaluationProvider()
    original = provider.parse

    def late_parse[T: ContractModel](
        *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        clock[0] += 2
        return original(
            system=system, user=user, output_type=output_type, timeout_seconds=timeout_seconds
        )

    monkeypatch.setattr(provider, "parse", late_parse)
    result = _run(provider, config=ReaderConfig(wall_clock_seconds=1))
    assert result.stop_reason == ReaderStop.WALL_CLOCK
    assert result.apply_entry is None


def test_provider_outage_propagates_for_the_activity_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport outage is not a reading: it must not persist a verification
    the outage produced, and it must not consume a schema repair either."""

    from countersign.gateway.provider import ProviderUnavailable

    provider = FakeEvaluationProvider()
    timeouts: list[float | None] = []

    def unavailable[T: ContractModel](
        *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        timeouts.append(timeout_seconds)
        raise ProviderUnavailable("service unavailable")

    monkeypatch.setattr(provider, "parse", unavailable)
    with pytest.raises(ProviderUnavailable):
        _run(provider, first_url=ATS_URL, first_html=ATS_HTML)
    assert len(timeouts) == 1 and 0 < (timeouts[0] or 0) <= 120


def test_claim_cited_on_a_superseded_observation_rebinds_or_is_rejected() -> None:
    """After a RENDER of the same URL the rendered page governs. A claim the
    model cites on the static page still counts when the rendered page shows
    it too, and is rejected with the reason when it does not."""

    static = b'<p>Position closed.</p><a href="/apply">Apply</a>'
    kept = b'<p>Rendered. Position closed.</p><a href="/apply">Apply</a>'
    gone = b"<p>Rendered without the button.</p>"

    def run(rendered: bytes) -> ReaderResult:
        def render(url: str, body: bytes, timeout: float) -> FetchResult | None:
            return _result(url, rendered)

        return _run(
            FakeEvaluationProvider(
                page_steps=[
                    PageStep(action=ReaderAction.RENDER, why="scripts", reading=_PROVISIONAL),
                    PageStep(
                        action=ReaderAction.DONE,
                        why="cites the static page",
                        reading=PageReading(
                            body_observation=0,
                            apply_entry=ApplyEntry(
                                kind=ApplyEntryKind.APPLY_CONTROL,
                                cited_text="Apply",
                                observation=0,
                                href="https://boards.greenhouse.io/apply",
                            ),
                            evidence=[
                                PageEvidence(
                                    kind=EvidenceKind.CLOSED_NOTICE,
                                    cited_text="Position closed.",
                                    observation=0,
                                )
                            ],
                        ),
                    ),
                ]
            ),
            first_url=ATS_URL,
            first_html=static,
            render=render,
            config=_STRICT,
        )

    result = run(kept)
    assert result.apply_entry is not None and result.apply_entry.observation == 1
    assert [e.observation for e in result.evidence] == [1]
    assert result.rejected_claims == []

    result = run(gone)
    assert result.apply_entry is None and result.evidence == []
    assert len(result.rejected_claims) == 2
    assert all("observation 1 superseded" in r.reason for r in result.rejected_claims)


def test_text_marker_needs_a_real_span_not_a_letter() -> None:
    def marker(text: str) -> ApplyEntry:
        return ApplyEntry(kind=ApplyEntryKind.TEXT_MARKER, cited_text=text, observation=0)

    for cited, expected in (("e", None), ("Python required", "Python required")):
        result = _run(
            FakeEvaluationProvider(
                page_steps=[
                    PageStep(
                        action=ReaderAction.DONE,
                        why="marker",
                        reading=PageReading(body_observation=0, apply_entry=marker(cited)),
                    )
                ]
            ),
            first_url=ATS_URL,
            first_html=ATS_HTML,
            config=_STRICT,
        )
        got = result.apply_entry.cited_text if result.apply_entry else None
        assert got == expected, cited


def test_render_failure_is_a_step_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    from countersign.capabilities.render import RenderError

    def render(url: str, body: bytes, timeout: float) -> FetchResult | None:
        raise RenderError("browser unavailable")

    result = _run(
        FakeEvaluationProvider(
            page_steps=[
                PageStep(action=ReaderAction.RENDER, why="shell", reading=_PROVISIONAL),
                PageStep(action=ReaderAction.DONE, why="no evidence", reading=_PROVISIONAL),
            ]
        ),
        render=render,
    )
    assert result.steps[0].outcome.startswith("render failed:")
    assert result.apply_entry is None


def test_jsonld_claim_must_name_the_actual_field_not_an_unrelated_date() -> None:
    html = b"""<p>Applications welcome in 2030-01-01.</p>
    <script type="application/ld+json">{"@type": "JobPosting", "directApply": false,
    "validThrough": "2020-01-01"}</script>"""
    result = _run(
        FakeEvaluationProvider(
            page_steps=[
                PageStep(
                    action=ReaderAction.DONE,
                    why="wrong fields",
                    reading=PageReading(
                        body_observation=0,
                        apply_entry=ApplyEntry(
                            kind=ApplyEntryKind.JSON_LD_DIRECT_APPLY,
                            cited_text="directApply: false",
                            observation=0,
                        ),
                        evidence=[
                            PageEvidence(
                                kind=EvidenceKind.VALID_THROUGH,
                                cited_text="2030-01-01",
                                observation=0,
                            )
                        ],
                    ),
                )
            ]
        ),
        first_html=html,
        config=_STRICT,
    )
    assert result.apply_entry is None
    assert len(result.rejected_claims) == 2
    assert [e.cited_text for e in result.evidence] == ["2020-01-01"]


@pytest.mark.parametrize(
    "entry",
    [
        ApplyEntry(
            kind=ApplyEntryKind.MAILTO,
            observation=0,
            cited_text="Apply now",
            href="mailto:careers@acme.example",
        ),
        ApplyEntry(
            kind=ApplyEntryKind.TEXT_MARKER,
            observation=0,
            cited_text=(
                "receiving your electronic application (CV, reference letters, etc.) by email to"
            ),
        ),
    ],
)
def test_company_fixture_apply_routes_verify(entry: ApplyEntry) -> None:
    from pathlib import Path

    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    body = (fixtures / "company_page_mailto_apply.html").read_bytes()
    result = _run(
        FakeEvaluationProvider(
            page_steps=[
                PageStep(
                    action=ReaderAction.DONE,
                    why="the posting says how to apply",
                    reading=PageReading(body_observation=0, apply_entry=entry),
                )
            ]
        ),
        first_url="https://www.acme.example/careers/platform-engineer/",
        first_html=body,
    )
    assert result.apply_entry == entry
    assert result.rejected_claims == []


def test_citation_from_the_shown_tail_verifies() -> None:
    """A span quoted from the shown tail verifies against the full text."""

    filler = " ".join(f"line {i} of the posting body." for i in range(1400))
    tail = "Send your application by email to careers@acme.example."
    html = f"<p>{filler}</p><p>{tail}</p>".encode()
    entry = ApplyEntry(kind=ApplyEntryKind.TEXT_MARKER, observation=0, cited_text=tail)
    result = _run(
        FakeEvaluationProvider(
            page_steps=[
                PageStep(
                    action=ReaderAction.DONE,
                    why="instruction in the tail",
                    reading=PageReading(body_observation=0, apply_entry=entry),
                )
            ]
        ),
        first_url=ATS_URL,
        first_html=html,
    )
    assert result.observations[0].dossier().text_truncated is True
    assert result.apply_entry == entry and result.rejected_claims == []


def _jsonld_page(fields: str) -> bytes:
    return (
        b"<p>Build things. Apply below.</p>"
        b'<script type="application/ld+json">{"@type": "JobPosting", '
        + fields.encode()
        + b"}</script>"
    )


@pytest.mark.parametrize(
    ("fields", "cited", "href", "accepted"),
    [
        ('"directApply": true', "directApply: true", None, True),
        ('"directApply": true', '"directApply": true', None, True),
        ('"directApply": true', "directApply = True", None, True),
        ('"directApply": true', "  directApply :  true ,", None, True),
        ('"directApply": true', "direct_apply: true", None, True),  # field-name spelling
        ('"directApply": true', '"direct_apply": true', None, True),
        ('"directApply": true', "notdirectApply: true", None, False),
        ('"directApply": true', "direct__apply: true", None, False),
        ('"directApply": true', "directApply: trueish", None, False),
        (
            '"directApply": true',
            '"directApply": false, "otherFlag": true',
            None,
            False,
        ),
        ('"directApply": false', '"directApply": true', None, False),  # fact is false
        (
            '"directApply": false, "otherFlag": true',
            '"directApply": false, "otherFlag": true',
            None,
            False,
        ),
        ('"directApply": true, "otherFlag": true', '"otherFlag": true', None, False),  # wrong key
        ('"directApply": true', '"directApply": "true"', "https://x.example", False),  # href
        ('"title": "Engineer"', "directApply: true", None, False),  # field missing
    ],
)
def test_direct_apply_citation_is_checked_against_the_fact(
    fields: str, cited: str, href: str | None, accepted: bool
) -> None:
    entry = ApplyEntry(
        kind=ApplyEntryKind.JSON_LD_DIRECT_APPLY, observation=0, cited_text=cited, href=href
    )
    result = _run(
        FakeEvaluationProvider(
            page_steps=[
                PageStep(
                    action=ReaderAction.DONE,
                    why="structured",
                    reading=PageReading(body_observation=0, apply_entry=entry),
                )
            ]
        ),
        first_url=ATS_URL,
        first_html=_jsonld_page(fields),
        config=_STRICT,
    )
    assert (result.apply_entry == entry) is accepted
    assert (result.rejected_claims == []) is accepted


@pytest.mark.parametrize(
    ("fields", "cited", "href", "accepted"),
    [
        ('"validThrough": "2030-01-31"', "2030-01-31", None, True),
        ('"validThrough": "2030-01-31"', '"validThrough": "2030-01-31"', None, True),
        (
            '"validThrough": "2030-01-31T23:59:00Z"',
            "validThrough: 2030-01-31T23:59:00Z",
            None,
            True,
        ),
        ('"validThrough": "2030-01-31"', "2030-01-30", None, False),  # wrong date
        ('"validThrough": "2030-01-31"', "2030-01", None, False),  # not the whole value
        ('"validThrough": "2030-01-31"', "2030-01-31", "https://x.example", False),  # href
        ('"title": "Engineer"', "2030-01-31", None, False),  # field missing
        ('"validThrough": ""', "validThrough: ", None, False),  # empty value
    ],
)
def test_valid_through_citation_must_contain_the_actual_value(
    fields: str, cited: str, href: str | None, accepted: bool
) -> None:
    claim = PageEvidence(
        kind=EvidenceKind.VALID_THROUGH, observation=0, cited_text=cited, href=href
    )
    result = _run(
        FakeEvaluationProvider(
            page_steps=[
                PageStep(
                    action=ReaderAction.DONE,
                    why="expiry",
                    reading=PageReading(body_observation=0, apply_entry=_NONE, evidence=[claim]),
                )
            ]
        ),
        first_url=ATS_URL,
        first_html=_jsonld_page(fields),
        config=_STRICT,
    )
    if accepted:
        assert result.rejected_claims == []
        # The model's accepted claim stands in for the code-added one: one per observation.
        assert [e for e in result.evidence if e.kind == EvidenceKind.VALID_THROUGH] == [claim]
    else:
        assert len(result.rejected_claims) == 1
        assert claim not in result.evidence


# --- citation repair (ADR-012 amendment) ---------------------------------

_MISQUOTED = PageReading(
    body_observation=0,
    apply_entry=ApplyEntry(
        kind=ApplyEntryKind.APPLY_CONTROL,
        cited_text="Apply",  # the label is "Apply for this Job"
        observation=0,
        href="https://boards.greenhouse.io/acme/jobs/1/apply",
    ),
)
_RECITED = PageReading(
    body_observation=0,
    apply_entry=ApplyEntry(
        kind=ApplyEntryKind.APPLY_CONTROL,
        cited_text="Apply for this Job",
        observation=0,
        href="https://boards.greenhouse.io/acme/jobs/1/apply",
    ),
)


def _done(reading: PageReading, why: str = "done") -> PageStep:
    return PageStep(action=ReaderAction.DONE, why=why, reading=reading)


def test_rejected_reading_is_asked_once_more_with_the_rejections_in_view() -> None:
    provider = FakeEvaluationProvider(page_steps=[_done(_MISQUOTED, "first"), _done(_RECITED)])
    seen: list[str] = []
    original = provider.parse

    def recording[T: ContractModel](
        *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        seen.append(user)
        return original(
            system=system, user=user, output_type=output_type, timeout_seconds=timeout_seconds
        )

    provider.parse = recording  # type: ignore[method-assign]
    result = _run(provider, first_url=ATS_URL, first_html=ATS_HTML)
    assert provider.calls == 2
    assert result.stop_reason == ReaderStop.DONE
    assert result.apply_entry == _RECITED.apply_entry
    assert result.rejected_claims == []
    assert [(s.step, s.action) for s in result.steps] == [
        (1, ReaderAction.DONE),
        (2, ReaderAction.DONE),
    ]
    assert result.steps[0].outcome.startswith("reading proposed; 1 claim rejected: ")
    assert "apply_entry APPLY_CONTROL 'Apply'" in result.steps[0].outcome
    assert result.steps[0].why == "first"
    assert result.steps[1].outcome == "reading proposed"
    # The second ask carries the first step, rejections included.
    assert "1 claim rejected" in seen[1] and "1 claim rejected" not in seen[0]


def test_citation_repair_happens_once_and_keeps_the_final_rejections() -> None:
    provider = FakeEvaluationProvider(page_steps=[_done(_MISQUOTED)] * 3)
    result = _run(provider, first_url=ATS_URL, first_html=ATS_HTML)
    assert provider.calls == 2
    assert result.stop_reason == ReaderStop.DONE
    assert result.apply_entry is None
    assert [r.cited_text for r in result.rejected_claims] == ["Apply"]
    assert [s.step for s in result.steps] == [1, 2]
    assert all("1 claim rejected" in s.outcome for s in result.steps)


def test_citation_repair_needs_a_step_left_in_the_budget() -> None:
    provider = FakeEvaluationProvider(page_steps=[_done(_MISQUOTED), _done(_RECITED)])
    result = _run(
        provider, first_url=ATS_URL, first_html=ATS_HTML, config=ReaderConfig(max_steps=1)
    )
    assert provider.calls == 1
    assert result.stop_reason == ReaderStop.DONE
    assert result.apply_entry is None
    assert len(result.rejected_claims) == 1


def test_failed_repair_step_keeps_the_reading_already_proposed() -> None:
    """The first DONE is a completed reading; a model failure on the extra ask
    is recorded as a step and does not discard what verified."""

    from countersign.gateway.provider import ContractFailure

    mixed = PageReading(
        body_observation=0,
        apply_entry=_RECITED.apply_entry,
        evidence=[
            PageEvidence(kind=EvidenceKind.CLOSED_NOTICE, cited_text="filled", observation=0)
        ],
    )
    provider = FakeEvaluationProvider(page_steps=[_done(mixed)])
    original = provider.parse

    def failing_second[T: ContractModel](
        *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        if provider.calls:
            provider.calls += 1
            raise ContractFailure("fake: no contract-valid output")
        return original(
            system=system, user=user, output_type=output_type, timeout_seconds=timeout_seconds
        )

    provider.parse = failing_second  # type: ignore[method-assign]
    result = _run(
        provider,
        first_url=ATS_URL,
        first_html=ATS_HTML,
        config=ReaderConfig(max_repairs=0),
    )
    assert result.stop_reason == ReaderStop.MODEL_FAILURE
    assert result.apply_entry == _RECITED.apply_entry
    assert result.evidence == []
    assert [(r.part, r.kind) for r in result.rejected_claims] == [("evidence", "CLOSED_NOTICE")]
    assert [s.action for s in result.steps] == [ReaderAction.DONE, ReaderAction.DONE]
    assert result.steps[1].outcome.startswith("MODEL_FAILURE")


def test_dossier_shows_jsonld_under_the_source_names() -> None:
    from countersign.agents.page_reader import build_user_prompt

    obs = observe(
        index=0,
        source_url=ATS_URL,
        how=ObservationHow.STATIC,
        authority=SourceAuthority.OFFICIAL_ATS,
        http_status=200,
        body=_jsonld_page('"directApply": true, "validThrough": "2030-01-31"'),
    )
    prompt = build_user_prompt([obs.dossier()], [], ReaderConfig())
    assert '"directApply": true' in prompt and '"validThrough": "2030-01-31"' in prompt
    assert "direct_apply" not in prompt and "valid_through" not in prompt


# --- unchanged-page reread (ticket 004) ----------------------------------

_PREVIOUS = PageReadingRecord(
    reading_id="read-c1-g1",
    case_id="c1",
    url=ATS_URL,
    observations=[
        ObservationRef(index=0, source_url=ATS_URL, how=ObservationHow.STATIC, http_status=200)
    ],
    body_observation=0,
    apply_entry=_RECITED.apply_entry,
    evidence=[],
    rejected_claims=[],
    steps=[],
    stop_reason=ReaderStop.DONE,
    prompt_version="page-reader/0.4.0",
    usage=None,
    read_at=datetime(2026, 10, 1, tzinfo=UTC),
)


def _fresh(html: bytes = ATS_HTML) -> Observation:
    return observe(
        index=0,
        source_url=ATS_URL,
        how=ObservationHow.STATIC,
        authority=SourceAuthority.OFFICIAL_ATS,
        http_status=200,
        body=html,
    )


def test_an_unchanged_page_keeps_the_previous_reading_without_the_model() -> None:
    page = _fresh()
    result = reread_unchanged(_PREVIOUS, page.normalized_hash, page)
    assert result is not None
    assert (result.stop_reason, result.usage, result.model_calls) == (ReaderStop.DONE, None, 0)
    assert result.prompt_version == "page-reader/0.4.0"
    assert result.observations == [page]
    assert result.apply_entry == _RECITED.apply_entry
    assert [s.outcome for s in result.steps] == ["unchanged since read-c1-g1"]


@pytest.mark.parametrize(
    ("changes", "html"),
    [
        ({}, ATS_HTML.replace(b"/acme/jobs/1/apply", b"/acme/jobs/1/apply?v=2")),  # href only
        ({}, ATS_HTML.replace(b"Python", b"Rust")),  # text hash
        ({"body_observation": 1}, ATS_HTML),
        ({"apply_entry": _RECITED.apply_entry.model_copy(update={"observation": 1})}, ATS_HTML),
        ({"stop_reason": ReaderStop.STEP_BUDGET}, ATS_HTML),
    ],
)
def test_a_changed_page_or_an_uncheckable_reading_is_read_again(
    changes: dict[str, Any], html: bytes
) -> None:
    previous = _PREVIOUS.model_copy(update=changes)
    assert reread_unchanged(previous, _fresh().normalized_hash, _fresh(html)) is None
