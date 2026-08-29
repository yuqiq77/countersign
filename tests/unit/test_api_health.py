"""Minimal API smoke test for the Slice 0 application factory."""

import pytest
from httpx import ASGITransport, AsyncClient

from countersign.api import create_app


@pytest.mark.asyncio
async def test_healthcheck() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "countersign-api"}
