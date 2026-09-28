"""Official-host mapping (ticket 002): explicit, exact, versioned; never inferred."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from countersign.agents.page_reader import read_page
from countersign.capabilities.http_fetch import FetchResult
from countersign.capabilities.normalize import (
    classify_authority,
    host_of,
    is_shared_host,
    normalize_host,
)
from countersign.contracts.enums import (
    ApplyEntryKind,
    JobStatus,
    ReaderAction,
    SourceAuthority,
)
from countersign.contracts.page_reading import ApplyEntry, PageReading, PageStep
from countersign.domain.verification import classify_reading
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.policy.official_hosts import (
    OFFICIAL_HOSTS_PATH_VAR,
    OfficialHostPolicy,
    load_official_hosts,
    parse_official_hosts,
)

MAPPING = json.dumps(
    {
        "version": "test-1",
        "entries": [
            {"host": "careers.acme.example", "company": "acme"},
            {"host": "www.acme.example", "company": "acme"},
        ],
    }
)
COMPANY_URL = "https://careers.acme.example/roles/1"
COMPANY_HTML = (
    b"<html><body><h1>Platform Engineer</h1><p>Build the platform.</p>"
    b'<a href="/roles/1/apply">Apply now</a></body></html>'
)
BOARD_URL = "https://board.example/jobs/42"
BOARD_HTML = (
    b"<html><body><h1>Platform Engineer at acme</h1><p>Posted by acme.</p>"
    b'<a href="https://careers.acme.example/roles/1">Official posting</a>'
    b'<a href="/jobs/42/apply">Apply on board</a></body></html>'
)


def _policy() -> OfficialHostPolicy:
    return parse_official_hosts(MAPPING)


def _write(tmp_path: Path, text: str) -> str:
    file = tmp_path / "official-hosts.json"
    file.write_text(text, encoding="utf-8")
    return str(file)


def test_unset_or_blank_path_means_no_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFICIAL_HOSTS_PATH_VAR, raising=False)
    assert load_official_hosts() is None
    monkeypatch.setenv(OFFICIAL_HOSTS_PATH_VAR, "   ")
    assert load_official_hosts() is None


def test_set_path_loads_and_validates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OFFICIAL_HOSTS_PATH_VAR, _write(tmp_path, MAPPING))
    policy = load_official_hosts()
    assert policy is not None
    assert policy.version == "test-1"
    assert len(policy.fingerprint) == 8 and int(policy.fingerprint, 16) >= 0
    assert dict(policy.hosts) == {"careers.acme.example": "acme", "www.acme.example": "acme"}
    with pytest.raises(TypeError):  # the loaded mapping is read-only
        policy.hosts["x.example"] = "x"  # type: ignore[index]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty"),
        ("   \n", "empty"),
        ("not json", "official hosts"),
        ('{"version": "v"}', "official hosts"),  # entries dropped in an edit
        ('{"version": "v", "entries": []}', "official hosts"),  # maps nothing
        (
            '{"version": "v", "entries": [{"host": "*.acme.example", "company": "a"}]}',
            "not a bare hostname",
        ),
        (
            '{"version": "v", "entries": [{"host": "-acme.example", "company": "a"}]}',
            "not a bare hostname",
        ),
        ('{"version": 1, "entries": []}', "official hosts"),  # strict: no coercion
        ('{"version": "v", "entries": [], "extra": 1}', "official hosts"),
        (
            '{"version": "v", "entries": [{"host": "acme.example/careers", "company": "a"}]}',
            "not a bare hostname",
        ),
        (
            '{"version": "v", "entries": [{"host": "https://acme.example", "company": "a"}]}',
            "not a bare hostname",
        ),
        (
            '{"version": "v", "entries": [{"host": "acme.example:8443", "company": "a"}]}',
            "not a bare hostname",
        ),
        (
            '{"version": "v", "entries": [{"host": "boards.greenhouse.io", "company": "a"}]}',
            "shared application-system or discovery host",
        ),
        (
            '{"version": "v", "entries": [{"host": "acme.jobs.lever.co", "company": "a"}]}',
            "shared application-system or discovery host",
        ),
        (
            '{"version": "v", "entries": [{"host": "www.linkedin.com", "company": "a"}]}',
            "shared application-system or discovery host",
        ),
        (
            '{"version": "v", "entries": [{"host": "Careers.Acme.example.", "company": "a"},'
            ' {"host": "careers.acme.example", "company": "b"}]}',
            "listed twice",
        ),
    ],
)
def test_malformed_conflicting_or_shared_entries_fail_visibly(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_official_hosts(text, source="test")


def test_missing_file_fails_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not a file"):
        load_official_hosts(str(tmp_path / "absent.json"))


def test_fingerprint_follows_content_not_surrounding_whitespace() -> None:
    same = parse_official_hosts(MAPPING)
    again = parse_official_hosts(MAPPING + "\n")
    other = parse_official_hosts(MAPPING.replace("test-1", "test-2"))
    assert same.fingerprint == again.fingerprint
    assert same.fingerprint != other.fingerprint
    assert other.version == "test-2"


def test_internationalized_hosts_meet_in_punycode() -> None:
    """The fetcher reports final URLs in punycode; an entry typed with
    non-ASCII letters must still match it."""

    policy = parse_official_hosts(
        '{"version": "v", "entries": [{"host": "jobs.bücher.example", "company": "books"}]}'
    )
    assert "jobs.xn--bcher-kva.example" in policy.hosts
    assert host_of("https://jobs.bücher.example/x") == "jobs.xn--bcher-kva.example"
    for url in (
        "https://jobs.xn--bcher-kva.example/roles/1",
        "https://jobs.bücher.example/roles/1",
    ):
        assert policy.classify(url) is SourceAuthority.OFFICIAL_COMPANY, url
        assert policy.entry_for(url) == "books"


def test_host_helpers() -> None:
    assert host_of("https://Careers.Acme.Example./roles/1?x=1") == "careers.acme.example"
    assert host_of("not a url") == ""
    assert normalize_host("  Careers.Acme.Example. ") == "careers.acme.example"
    for bad in ("", " ", "acme.example/x", "acme.example:443", "a@acme.example", "a b.example"):
        with pytest.raises(ValueError):
            normalize_host(bad)
    assert is_shared_host("jobs.lever.co") and is_shared_host("acme.jobs.lever.co")
    assert is_shared_host("linkedin.com") and not is_shared_host("acme.example")


def test_classification_matches_the_exact_normalized_host_only() -> None:
    policy = _policy()
    official = SourceAuthority.OFFICIAL_COMPANY
    unofficial = SourceAuthority.COMPANY_CONTROLLED
    assert policy.classify("https://careers.acme.example/roles/1") is official
    assert policy.classify("https://Careers.ACME.example./roles/1") is official
    assert policy.classify("https://www.acme.example/jobs") is official
    for url in (
        "https://acme.example/careers",  # parent not listed
        "https://jobs.careers.acme.example/1",  # unlisted subdomain
        "https://careers.acme.example.evil.example/1",  # suffix look-alike
        "https://notcareers.acme.example/1",
        "https://careers-acme.example/1",
        "https://board.example/jobs?company=careers.acme.example",  # host only
    ):
        assert policy.classify(url) is unofficial, url
    # ATS and discovery hosts are classified exactly as before.
    assert (
        policy.classify("https://boards.greenhouse.io/acme/jobs/1") is SourceAuthority.OFFICIAL_ATS
    )
    assert policy.classify("https://www.linkedin.com/jobs/view/1") is SourceAuthority.DISCOVERY_ONLY
    assert policy.entry_for("https://www.acme.example/jobs") == "acme"
    assert policy.entry_for("https://acme.example/jobs") is None
    # Without a mapping nothing changes.
    assert classify_authority("https://careers.acme.example/roles/1") is unofficial
    assert classify_authority("https://careers.acme.example/roles/1", None) is unofficial


def _fetch_result(url: str, body: bytes, final_url: str | None = None) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=final_url or url,
        http_status=200,
        content_type="text/html",
        body=body,
        redirect_chain=[],
    )


def _done(reading: PageReading) -> PageStep:
    return PageStep(action=ReaderAction.DONE, why="scripted", reading=reading)


def _no_fetch(url: str, timeout: float) -> FetchResult:
    raise AssertionError("no navigation in this test")


def _no_render(url: str, body: bytes, timeout: float) -> FetchResult | None:
    return None


def _status(
    url: str, body: bytes, reading: PageReading, policy: OfficialHostPolicy | None
) -> tuple[JobStatus, SourceAuthority]:
    result = read_page(
        url,
        first=_fetch_result(url, body),
        authority_of=policy.classify if policy is not None else classify_authority,
        fetch=_no_fetch,
        render=_no_render,
        provider=FakeEvaluationProvider(page_steps=[_done(reading)]),
    )
    chosen, status, _ = classify_reading(
        [o.dossier() for o in result.observations],
        result.apply_entry,
        result.evidence,
        now=datetime.now(UTC),
    )
    return status, result.observations[chosen].authority


_COMPANY_APPLY = PageReading(
    body_observation=0,
    apply_entry=ApplyEntry(
        kind=ApplyEntryKind.APPLY_CONTROL,
        cited_text="Apply now",
        observation=0,
        href="https://careers.acme.example/roles/1/apply",
    ),
)
_NO_ENTRY = PageReading(body_observation=0, apply_entry=ApplyEntry(kind=ApplyEntryKind.NONE))


def test_mapped_company_page_with_a_cited_apply_control_is_official_and_open() -> None:
    """The failing test the ticket starts with: the same page, mapped and not."""

    assert _status(COMPANY_URL, COMPANY_HTML, _COMPANY_APPLY, _policy()) == (
        JobStatus.OPEN,
        SourceAuthority.OFFICIAL_COMPANY,
    )
    assert _status(COMPANY_URL, COMPANY_HTML, _COMPANY_APPLY, None) == (
        JobStatus.OPEN_THIRD_PARTY_ONLY,
        SourceAuthority.COMPANY_CONTROLLED,
    )


def test_mapping_plus_http_200_is_not_open() -> None:
    assert _status(COMPANY_URL, COMPANY_HTML, _NO_ENTRY, _policy()) == (
        JobStatus.UNVERIFIED,
        SourceAuthority.OFFICIAL_COMPANY,
    )


def test_a_board_naming_the_mapped_employer_stays_a_board() -> None:
    board_apply = PageReading(
        body_observation=0,
        apply_entry=ApplyEntry(
            kind=ApplyEntryKind.APPLY_CONTROL,
            cited_text="Apply on board",
            observation=0,
            href="https://board.example/jobs/42/apply",
        ),
    )
    assert _status(BOARD_URL, BOARD_HTML, board_apply, _policy()) == (
        JobStatus.OPEN_THIRD_PARTY_ONLY,
        SourceAuthority.COMPANY_CONTROLLED,
    )


def test_observations_are_classified_by_their_landing_host() -> None:
    """A mapped start URL confers nothing on an unmapped destination, and an
    unmapped start URL that lands on a mapped host is official there."""

    policy = _policy()

    def landing(requested: str, final: str) -> SourceAuthority:
        result = read_page(
            requested,
            first=_fetch_result(requested, COMPANY_HTML, final_url=final),
            authority_of=policy.classify,
            fetch=_no_fetch,
            render=_no_render,
            provider=FakeEvaluationProvider(page_steps=[_done(_NO_ENTRY)]),
        )
        return result.observations[0].authority

    assert landing("https://board.example/go/1", COMPANY_URL) is SourceAuthority.OFFICIAL_COMPANY
    assert (
        landing(COMPANY_URL, "https://elsewhere.example/roles/1")
        is SourceAuthority.COMPANY_CONTROLLED
    )


def test_unreadable_file_is_a_configuration_error(tmp_path: Path) -> None:
    """An OSError from the file system is the loader's ValueError, so the
    Activity turns it into the non-retryable PolicyError like any other
    broken configuration."""

    import os

    if os.geteuid() == 0:
        pytest.skip("root reads unreadable files")
    file = tmp_path / "official-hosts.json"
    file.write_text(MAPPING, encoding="utf-8")
    file.chmod(0)
    try:
        with pytest.raises(ValueError, match="cannot be read"):
            load_official_hosts(str(file))
    finally:
        file.chmod(0o600)
