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
from countersign.contracts.enums import ApprovalDecision, ApprovalRequestStatus
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


def _seed_approval(*, allowed: list[ApprovalDecision], status: ApprovalRequestStatus) -> str:
    with session_scope() as session:
        repo.create_case(session, case_id="c1", url=PUBLIC_URL)
        row = repo.create_approval_request(
            session,
            case_id="c1",
            generation=1,
            allowed=allowed,
            expires_at=repo.utcnow() + timedelta(days=7),
        )
        row.status = status.value
        return row.id


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
