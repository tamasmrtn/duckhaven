"""The audit context middleware: every response names its request."""

from __future__ import annotations

from api.services.audit.context import REQUEST_ID_HEADER


async def test_every_response_carries_a_request_id(client):
    first = await client.get("/healthz")
    second = await client.get("/healthz")

    assert len(first.headers[REQUEST_ID_HEADER]) == 32
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


async def test_a_refused_request_carries_one_too(client):
    resp = await client.get("/me")

    assert resp.status_code == 401
    assert resp.headers[REQUEST_ID_HEADER]
