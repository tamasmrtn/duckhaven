"""GET /dashboard-schema: the file format, for editors and clients."""

from httpx import AsyncClient

from api.services.dashboards.spec import json_schema


async def test_the_schema_is_served_without_signing_in(client: AsyncClient):
    resp = await client.get("/dashboard-schema")
    assert resp.status_code == 200
    assert resp.json() == json_schema()
    assert resp.headers["cache-control"] == "public, max-age=3600"


async def test_a_version_can_be_asked_for_by_number(client: AsyncClient):
    assert (await client.get("/dashboard-schema", params={"version": 1})).status_code == 200


async def test_a_version_the_server_does_not_read_is_refused(client: AsyncClient):
    resp = await client.get("/dashboard-schema", params={"version": 2})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "unsupported_spec_version"
    assert "version 1, not 2" in body["message"]
