"""Extraction gives the model facts without judging controls by keywords."""

from countersign.capabilities.page_evidence import MAX_CONTROLS, Observation, observe
from countersign.contracts.enums import ObservationHow, SourceAuthority


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


def test_controls_are_one_per_target_keeping_the_first_real_label() -> None:
    obs = _observe(b"""
      <a href="/apply"><img src="logo.png"></a>
      <a href="/apply">Apply</a>
      <a href="/apply">Apply now</a>
      <button>Save job</button><button>Save job</button>
      <a href="/privacy">Privacy</a><a href="/privacy">Privacy</a>
    """)
    assert [(c.text, c.href) for c in obs.controls] == [
        ("Apply", "https://acme.example/apply"),
        ("Save job", None),
        ("Privacy", "https://acme.example/privacy"),
    ]
    assert obs.controls_truncated is False


def test_control_list_is_capped_in_document_order_and_says_so() -> None:
    links = b"".join(f'<a href="/nav/{i}">Link {i}</a>'.encode() for i in range(MAX_CONTROLS + 30))
    obs = _observe(links + b'<a href="/apply">Apply</a>')
    assert len(obs.controls) == MAX_CONTROLS
    assert obs.controls[-1].text == f"Link {MAX_CONTROLS - 1}"
    assert obs.controls_truncated is True
    assert obs.dossier().controls_truncated is True
    assert not any(c.text == "Apply" for c in obs.controls)  # cut, and the dossier says so
