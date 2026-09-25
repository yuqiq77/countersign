"""Extraction gives the model facts without judging controls by keywords."""

from pathlib import Path

from countersign.capabilities.page_evidence import (
    MAX_CONTROLS,
    MAX_EXCERPT_CHARS,
    Observation,
    observe,
)
from countersign.contracts.enums import ObservationHow, SourceAuthority

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _observe(html: bytes) -> Observation:
    return observe(
        index=0,
        source_url="https://acme.example/jobs/1",
        how=ObservationHow.STATIC,
        authority=SourceAuthority.COMPANY_CONTROLLED,
        http_status=200,
        body=html,
    )


def test_controls_keep_context_and_absolute_targets_but_skip_scripts() -> None:
    obs = _observe(b"""
      <a href="/apply">I am interested</a><button>Apply later</button>
      <a href="/assets">stylesheet is already loaded</a>
      <script>var a = "<a href='/x'>fake</a>";</script>
      <a href="mailto:friend@example.com">Share by email</a>
    """)
    assert [c.text for c in obs.controls] == [
        "I am interested",
        "Apply later",
        "stylesheet is already loaded",
        "Share by email",
    ]
    assert obs.controls[0].href == "https://acme.example/apply"


def test_jsonld_facts_preserve_direct_apply_false_and_expiry() -> None:
    obs = _observe(b"""
      <script type="application/ld+json">{"@graph": [{"@type": "JobPosting",
      "title": "Engineer", "hiringOrganization": {"name": "Acme"},
      "directApply": false, "validThrough": "2030-01-31"}]}</script>
    """)
    assert obs.jsonld is not None and obs.jsonld.direct_apply is False
    assert obs.jsonld.valid_through == "2030-01-31"
    assert "directApply: false" in obs.citable_spans()
    assert obs.controls == []


def test_accessible_labels_and_submit_values_survive_navigation_links() -> None:
    navigation = b"".join(f'<a href="/nav/{i}">Nav {i}</a>'.encode() for i in range(50))
    html = (
        navigation
        + b"""
      <a aria-label="Apply" href="./apply?jobId=1"><span></span></a>
      <input type="submit" value="Send application">
      <a href="/unlabelled"><svg></svg></a>
    """
    )
    controls = _observe(html).controls
    assert any(
        c.text == "Apply" and c.href == "https://acme.example/jobs/apply?jobId=1" for c in controls
    )
    assert any(c.text == "Send application" for c in controls)
    assert any(c.href == "https://acme.example/unlabelled" for c in controls)


def test_only_exact_label_target_duplicates_merge() -> None:
    """Two labels on one target carry different meaning (a bare address link
    and an "Apply now" button on the same mailto); merging by target once
    hid the button from the model on a live page."""

    obs = _observe(b"""
      <a href="/apply"><img src="logo.png"></a>
      <a href="/apply">Apply</a>
      <a href="/apply">Apply now</a>
      <button>Save job</button><button>Save job</button>
      <a href="/privacy">Privacy</a><a href="/privacy">Privacy</a>
    """)
    assert [(c.text, c.href) for c in obs.controls] == [
        ("", "https://acme.example/apply"),
        ("Apply", "https://acme.example/apply"),
        ("Apply now", "https://acme.example/apply"),
        ("Save job", None),
        ("Privacy", "https://acme.example/privacy"),
    ]
    assert obs.controls_truncated is False


def test_excerpt_keeps_head_and_tail_verbatim_and_marks_the_gap() -> None:
    filler = " ".join(f"line {i} of the posting body." for i in range(1400))
    tail = "We look forward to receiving your application by email to careers@acme.example."
    obs = _observe(f"<p>{filler}</p><p>{tail}</p>".encode())
    assert len(obs.text) > MAX_EXCERPT_CHARS
    dossier = obs.dossier()
    assert dossier.text_truncated is True
    assert dossier.text_excerpt.startswith(obs.text[:12_000])
    assert dossier.text_excerpt.endswith(obs.text[-4_000:])
    omitted = len(obs.text) - 12_000 - 4_000
    assert f"[… {omitted} characters omitted …]" in dossier.text_excerpt
    assert tail in dossier.text_excerpt
    short = _observe(b"<p>short posting</p>").dossier()
    assert (short.text_excerpt, short.text_truncated) == ("short posting", False)


def test_company_fixture_dossier_retains_the_instruction_and_both_mailto_labels() -> None:
    """Regression for a live company page: the application instruction near
    the end and the "Apply now" button on an already-linked mailto."""

    obs = _observe((FIXTURES / "company_page_mailto_apply.html").read_bytes())
    dossier = obs.dossier()
    assert dossier.text_truncated is False
    assert "receiving your electronic application" in dossier.text_excerpt
    assert obs.text.find("receiving your electronic application") > 6_000  # the old cap
    mailto = [(c.text, c.href) for c in dossier.controls if (c.href or "").startswith("mailto:")]
    assert mailto == [
        ("careers@acme.example", "mailto:careers@acme.example"),
        ("Apply now", "mailto:careers@acme.example"),
    ]


def test_control_list_is_capped_in_document_order_and_says_so() -> None:
    links = b"".join(f'<a href="/nav/{i}">Link {i}</a>'.encode() for i in range(MAX_CONTROLS + 30))
    obs = _observe(links + b'<a href="/apply">Apply</a>')
    assert len(obs.controls) == MAX_CONTROLS
    assert obs.controls[-1].text == f"Link {MAX_CONTROLS - 1}"
    assert obs.controls_truncated is True
    assert obs.dossier().controls_truncated is True
    assert not any(c.text == "Apply" for c in obs.controls)  # cut, and the dossier says so
