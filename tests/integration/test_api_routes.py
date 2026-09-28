"""API behavior: idempotency fingerprints, decision lifecycle, URL policy.

Temporal is stubbed at the client seam; the database, contracts, and gates
are real.
"""

from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import countersign.api.routes as routes
from countersign.api.app import create_app
from countersign.contracts.enums import (
    ApprovalDecision,
    ApprovalRequestStatus,
    JobStatus,
    SourceAuthority,
)
from countersign.contracts.verification import VerificationResult
from countersign.persistence import repo
from countersign.persistence.db import session_scope

PUBLIC_URL = "https://93.184.216.34/jobs/1"


class _StubHandle:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def signal(self, *args: Any, **kwargs: Any) -> None:
        self._log.append("signal")


class _StubTemporal:
    def __init__(self) -> None:
        self.signals: list[str] = []
        self.starts: list[str] = []

    async def start_workflow(self, *args: Any, **kwargs: Any) -> None:
        self.starts.append(kwargs.get("id", "?"))

    def get_workflow_handle(self, workflow_id: str) -> _StubHandle:
        return _StubHandle(self.signals)


@pytest.fixture
def stub_temporal(monkeypatch: pytest.MonkeyPatch) -> _StubTemporal:
    stub = _StubTemporal()
    monkeypatch.setattr(routes, "_client", stub)
    return stub


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t")


@pytest.mark.usefixtures("db")
async def test_create_requires_idempotency_key(stub_temporal: _StubTemporal) -> None:
    async with _client() as client:
        response = await client.post("/v1/cases", json={"url": PUBLIC_URL})
    assert response.status_code == 400


@pytest.mark.usefixtures("db")
async def test_intake_rejects_policy_violating_urls(
    stub_temporal: _StubTemporal,
) -> None:
    async with _client() as client:
        for url in ("file:///etc/passwd", "http://10.0.0.8/internal", "http://[::1]/"):
            response = await client.post(
                "/v1/cases", json={"url": url}, headers={"Idempotency-Key": "k1"}
            )
            assert response.status_code == 422, url
    assert stub_temporal.starts == []


@pytest.mark.usefixtures("db")
async def test_idempotent_replay_restarts_the_workflow(
    stub_temporal: _StubTemporal,
) -> None:
    """A replayed key returns the same case AND re-issues the (idempotent)
    workflow start, healing a case whose first start failed."""

    async with _client() as client:
        first = await client.post(
            "/v1/cases", json={"url": PUBLIC_URL}, headers={"Idempotency-Key": "k1"}
        )
        second = await client.post(
            "/v1/cases", json={"url": PUBLIC_URL}, headers={"Idempotency-Key": "k1"}
        )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["case_id"] == second.json()["case_id"]
    assert len(stub_temporal.starts) == 2
    with session_scope() as session:
        assert len(repo.list_cases(session)) == 1


@pytest.mark.usefixtures("db")
async def test_idempotent_replay_of_a_started_case_starts_nothing(
    stub_temporal: _StubTemporal,
) -> None:
    """Once a case has left DISCOVERED its workflow exists — under this id
    format or the earlier per-generation one — so a replayed intake returns
    the case and must not start a second workflow for it."""

    from countersign.contracts.enums import ActorType, CaseState

    async with _client() as client:
        first = await client.post(
            "/v1/cases", json={"url": PUBLIC_URL}, headers={"Idempotency-Key": "k1"}
        )
    case_id = first.json()["case_id"]
    with session_scope() as session:
        repo.transition_case(
            session,
            case_id=case_id,
            new_state=CaseState.SNAPSHOTTED,
            actor_type=ActorType.SERVICE,
            actor_id="worker",
        )
    async with _client() as client:
        replay = await client.post(
            "/v1/cases", json={"url": PUBLIC_URL}, headers={"Idempotency-Key": "k1"}
        )
    assert replay.status_code == 202
    assert replay.json() == {
        "case_id": case_id,
        "workflow_id": f"job-case/{case_id}",
        "state": "SNAPSHOTTED",
    }
    assert len(stub_temporal.starts) == 1


@pytest.mark.usefixtures("db")
async def test_key_reuse_with_different_payload_conflicts(
    stub_temporal: _StubTemporal,
) -> None:
    async with _client() as client:
        first = await client.post(
            "/v1/cases", json={"url": PUBLIC_URL}, headers={"Idempotency-Key": "k1"}
        )
        conflict = await client.post(
            "/v1/cases",
            json={"url": "https://93.184.216.35/other"},
            headers={"Idempotency-Key": "k1"},
        )
    assert first.status_code == 202
    assert conflict.status_code == 409


def _seed_approval(
    *,
    allowed: list[ApprovalDecision],
    status: ApprovalRequestStatus,
    verification: JobStatus | None = None,
    generation: int = 1,
) -> str:
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url=PUBLIC_URL)
        verification_id: str | None = None
        if verification is not None:
            verification_id = "ver-c1-g1"
            repo.record_verification(
                session,
                verification_id=verification_id,
                result=VerificationResult(
                    case_id="c1",
                    status=verification,
                    authority=SourceAuthority.OFFICIAL_ATS,
                    checked_at=repo.utcnow(),
                    http_status=200,
                    final_url=PUBLIC_URL,
                    reason="seeded",
                    apply_markers_found=verification == JobStatus.OPEN,
                    closed_markers_found=verification == JobStatus.CLOSED,
                ),
            )
        row = repo.create_approval_request(
            session,
            case_id="c1",
            generation=generation,
            allowed=allowed,
            expires_at=repo.utcnow() + timedelta(days=7),
            verification_id=verification_id,
        )
        row.status = status.value
        return row.id


_GATED = [ApprovalDecision.REJECT, ApprovalDecision.REQUEST_EVIDENCE]


@pytest.mark.usefixtures("db")
async def test_decision_not_in_allowed_set_is_rejected(
    stub_temporal: _StubTemporal,
) -> None:
    """The lifecycle gate is enforced by the API, not just displayed."""

    approval_id = _seed_approval(
        allowed=[ApprovalDecision.REJECT, ApprovalDecision.REQUEST_EVIDENCE],
        status=ApprovalRequestStatus.PENDING,
    )
    async with _client() as client:
        response = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={"decision": "APPROVE", "reason": "please", "decision_version": 1},
        )
    assert response.status_code == 400
    assert stub_temporal.signals == []


@pytest.mark.usefixtures("db")
async def test_decision_on_expired_approval_is_410(
    stub_temporal: _StubTemporal,
) -> None:
    approval_id = _seed_approval(
        allowed=[ApprovalDecision.APPROVE, ApprovalDecision.REJECT],
        status=ApprovalRequestStatus.EXPIRED,
    )
    async with _client() as client:
        response = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={"decision": "APPROVE", "reason": "too late", "decision_version": 1},
        )
    assert response.status_code == 410
    assert stub_temporal.signals == []


@pytest.mark.usefixtures("db")
async def test_decision_replay_is_idempotent_and_conflicts_are_explicit(
    stub_temporal: _StubTemporal,
) -> None:
    approval_id = _seed_approval(
        allowed=[ApprovalDecision.APPROVE, ApprovalDecision.REJECT],
        status=ApprovalRequestStatus.PENDING,
    )
    with session_scope() as session:
        repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason="looks good",
            actor="admin",
            decision_version=1,
        )
    async with _client() as client:
        replay = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={"decision": "APPROVE", "reason": "looks good", "decision_version": 1},
        )
        conflict = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={"decision": "REJECT", "reason": "changed", "decision_version": 1},
        )
        later_version_on_decided = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={"decision": "REJECT", "reason": "again", "decision_version": 2},
        )
    assert replay.status_code == 200
    assert replay.json()["accepted"] is True
    assert conflict.status_code == 409
    assert later_version_on_decided.status_code == 409
    assert stub_temporal.signals == []  # replays never re-signal


@pytest.mark.usefixtures("db")
async def test_quoted_decision_version_is_rejected(
    stub_temporal: _StubTemporal,
) -> None:
    approval_id = _seed_approval(
        allowed=[ApprovalDecision.APPROVE],
        status=ApprovalRequestStatus.PENDING,
    )
    async with _client() as client:
        response = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={"decision": "APPROVE", "reason": "ok", "decision_version": "1"},
        )
    assert response.status_code == 422


@pytest.mark.usefixtures("db")
async def test_approve_on_an_unverified_posting_needs_nothing_typed(
    stub_temporal: _StubTemporal,
) -> None:
    """ADR-013 as amended: the stored allowed set is the whole gate. Approving
    a posting the system could not verify is the reviewer's own verification,
    derived and recorded by the decision Activity, never typed here."""

    approval_id = _seed_approval(
        allowed=[ApprovalDecision.APPROVE, *_GATED],
        status=ApprovalRequestStatus.PENDING,
        verification=JobStatus.UNVERIFIED,
    )
    async with _client() as client:
        response = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={"decision": "APPROVE", "reason": "fits", "decision_version": 1},
        )
    assert response.status_code == 200 and response.json()["accepted"] is True
    assert stub_temporal.signals == ["signal"]


@pytest.mark.usefixtures("db")
async def test_decision_outside_the_stored_allowed_set_is_refused(
    stub_temporal: _StubTemporal,
) -> None:
    """The set was computed against the pinned verification when the request
    was created; the API neither widens nor recomputes it at decision time."""

    approval_id = _seed_approval(
        allowed=_GATED, status=ApprovalRequestStatus.PENDING, verification=JobStatus.CLOSED
    )
    async with _client() as client:
        response = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={"decision": "APPROVE", "reason": "I insist", "decision_version": 1},
        )
    assert response.status_code == 400
    assert stub_temporal.signals == []


@pytest.mark.usefixtures("db")
async def test_retired_attestation_field_is_rejected_at_the_boundary(
    stub_temporal: _StubTemporal,
) -> None:
    approval_id = _seed_approval(
        allowed=[ApprovalDecision.APPROVE, *_GATED],
        status=ApprovalRequestStatus.PENDING,
        verification=JobStatus.UNVERIFIED,
    )
    async with _client() as client:
        response = await client.post(
            f"/v1/approvals/{approval_id}/decisions",
            json={
                "decision": "APPROVE",
                "reason": "fits",
                "decision_version": 1,
                "attestation": {"observed_open": True, "note": "form loaded"},
            },
        )
    assert response.status_code == 422
    assert stub_temporal.signals == []


@pytest.mark.usefixtures("db")
async def test_decision_replay_compares_decision_reason_and_actor(
    stub_temporal: _StubTemporal,
) -> None:
    approval_id = _seed_approval(
        allowed=[ApprovalDecision.APPROVE, *_GATED],
        status=ApprovalRequestStatus.PENDING,
        verification=JobStatus.UNVERIFIED,
    )
    with session_scope() as session:
        repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVE,
            reason="fits",
            actor="admin",
            decision_version=1,
            approved_without_official_open=True,
        )
    url = f"/v1/approvals/{approval_id}/decisions"
    base = {"decision": "APPROVE", "reason": "fits", "decision_version": 1}
    async with _client() as client:
        replay = await client.post(url, json=base)
        other_reason = await client.post(url, json={**base, "reason": "different"})
        other_actor = await client.post(url, json={**base, "actor": "someone-else"})
    assert replay.status_code == 200
    assert other_reason.status_code == 409
    assert other_actor.status_code == 409
    assert stub_temporal.signals == []


@pytest.mark.usefixtures("db")
async def test_request_evidence_is_accepted_below_the_cap_and_refused_at_it(
    stub_temporal: _StubTemporal,
) -> None:
    from countersign.domain.gates import MAX_GENERATIONS

    below = _seed_approval(allowed=_GATED, status=ApprovalRequestStatus.PENDING, generation=1)
    body = {"decision": "REQUEST_EVIDENCE", "reason": "a note", "decision_version": 1}
    async with _client() as client:
        accepted = await client.post(f"/v1/approvals/{below}/decisions", json=body)
    assert accepted.status_code == 200
    assert stub_temporal.signals == ["signal"]

    with session_scope() as session:  # the gate at the cap omits REQUEST_EVIDENCE
        capped = repo.create_approval_request(
            session,
            case_id="c1",
            generation=MAX_GENERATIONS,
            allowed=[ApprovalDecision.REJECT],
            expires_at=repo.utcnow() + timedelta(days=7),
        ).id
    async with _client() as client:
        refused = await client.post(f"/v1/approvals/{capped}/decisions", json=body)
    assert refused.status_code == 400
    assert stub_temporal.signals == ["signal"]


@pytest.mark.usefixtures("db")
async def test_case_detail_exposes_operator_context_and_in_flight(
    stub_temporal: _StubTemporal,
) -> None:
    from countersign.contracts.enums import ActorType, CaseState
    from countersign.domain.ids import verification_id

    approval_id = _seed_approval(allowed=_GATED, status=ApprovalRequestStatus.PENDING)
    with session_scope() as session:
        for state in (
            CaseState.SNAPSHOTTED,
            CaseState.NORMALIZED,
            CaseState.DEDUPLICATED,
            CaseState.OFFICIAL_SOURCE_VERIFIED,
            CaseState.EVALUATED,
            CaseState.PENDING_APPROVAL,
        ):
            repo.transition_case(
                session,
                case_id="c1",
                new_state=state,
                actor_type=ActorType.SERVICE,
                actor_id="worker",
            )
        repo.record_decision(
            session,
            approval_request_id=approval_id,
            decision=ApprovalDecision.REQUEST_EVIDENCE,
            reason="a candidate-side note",
            actor="admin",
            decision_version=1,
        )
        repo.transition_case(
            session,
            case_id="c1",
            new_state=CaseState.NEEDS_MORE_EVIDENCE,
            actor_type=ActorType.HUMAN,
            actor_id="admin",
        )
    async with _client() as client:
        legacy = (await client.get("/v1/cases/c1")).json()
    # Decided before the loop existed: no continued-round record, so final.
    assert legacy["in_flight"] is False
    assert legacy["evaluations"] == [] and legacy["evaluation"] is None
    with session_scope() as session:
        repo.mark_round_continued(session, case_id="c1", next_generation=2)
    async with _client() as client:
        during = (await client.get("/v1/cases/c1")).json()
    assert during["in_flight"] is True
    assert [c["text"] for c in during["operator_context"]] == ["a candidate-side note"]
    assert during["approval"]["generation"] == 1
    assert during["approval"]["max_generations"] >= 2

    with session_scope() as session:
        repo.record_verification(
            session,
            verification_id=verification_id("c1", 2),
            result=VerificationResult(
                case_id="c1",
                status=JobStatus.UNVERIFIED,
                authority=SourceAuthority.OFFICIAL_ATS,
                checked_at=repo.utcnow(),
                http_status=200,
                final_url=PUBLIC_URL,
                reason="empty body",
                apply_markers_found=False,
                closed_markers_found=False,
            ),
        )
    async with _client() as client:
        after = (await client.get("/v1/cases/c1")).json()
    assert after["in_flight"] is False


@pytest.mark.usefixtures("db")
async def test_case_detail_shows_the_authority_the_reading_gave_the_body(
    stub_temporal: _StubTemporal,
) -> None:
    """Ticket 002: a reused snapshot's retrieval-time stamp can predate the
    official-host mapping; the case page shows what the reading decided."""

    from countersign.contracts.enums import ObservationHow, ReaderStop, SourceAuthority
    from countersign.contracts.page_reading import ObservationRef, PageReadingRecord
    from countersign.contracts.provenance import SnapshotRecord

    with session_scope() as session:
        repo.create_case(session, case_id="c1", url=PUBLIC_URL)
        repo.record_snapshot(
            session,
            SnapshotRecord(
                snapshot_id="snap-1",
                case_id="c1",
                canonical_url=PUBLIC_URL,
                final_url=PUBLIC_URL,
                source_type="pasted_url",
                authority_level=SourceAuthority.COMPANY_CONTROLLED,
                retrieved_at=repo.utcnow(),
                http_status=200,
                content_type="text/html",
                content_hash="abc",
                raw_object_key="blob-1",
                extractor_name="html-text",
                extractor_version="1.2.0",
                redirect_chain=[],
            ),
        )
        repo.record_page_reading(
            session,
            PageReadingRecord(
                reading_id="read-c1-g1",
                case_id="c1",
                url=PUBLIC_URL,
                observations=[
                    ObservationRef(
                        index=0,
                        source_url=PUBLIC_URL,
                        how=ObservationHow.STATIC,
                        http_status=200,
                        snapshot_id="snap-1",
                        authority=SourceAuthority.OFFICIAL_COMPANY,
                        official_host_entry="acme",
                    )
                ],
                body_observation=0,
                apply_entry=None,
                evidence=[],
                rejected_claims=[],
                steps=[],
                stop_reason=ReaderStop.DONE,
                prompt_version="page-reader/0.5.0",
                usage=None,
                read_at=repo.utcnow(),
                authority_policy_version="api-1",
                authority_policy_fingerprint="0123abcd",
                body_text_chars=12,
            ),
        )
    async with _client() as client:
        detail = (await client.get("/v1/cases/c1")).json()
    assert detail["snapshot"]["snapshot_id"] == "snap-1"
    # The provenance record keeps its retrieval-time stamp; the reading's
    # judgment is its own field.
    assert detail["snapshot"]["authority_level"] == "COMPANY_CONTROLLED"
    assert detail["posting_authority"] == "OFFICIAL_COMPANY"
