"""Unit tests for `api.services.unity_catalog.UCClient`.

Uses respx to mock the underlying httpx transport. Covers the happy
path for every method we'll call from production code, plus the
status-code dispatch (404 → NotFound, 409 → Conflict, 400-with-exists →
Conflict, other 4xx → BadRequest, 5xx → ServerError).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from api.services.unity_catalog import (
    UCBadRequestError,
    UCClient,
    UCConflictError,
    UCServerError,
)

BASE = "http://uc.test"


@pytest.fixture
async def uc() -> UCClient:
    return UCClient(base_url=BASE, token="t-secret", timeout_s=1.0)


@respx.mock
async def test_create_catalog_happy(uc: UCClient) -> None:
    respx.post(f"{BASE}/api/2.1/unity-catalog/catalogs").mock(
        return_value=httpx.Response(200, json={"name": "ws_alpha"})
    )
    cat = await uc.create_catalog("ws_alpha")
    assert cat.name == "ws_alpha"


@respx.mock
async def test_get_catalog_happy(uc: UCClient) -> None:
    respx.get(f"{BASE}/api/2.1/unity-catalog/catalogs/ws_alpha").mock(
        return_value=httpx.Response(200, json={"name": "ws_alpha"})
    )
    cat = await uc.get_catalog("ws_alpha")
    assert cat.name == "ws_alpha"


@respx.mock
async def test_catalog_exists_true(uc: UCClient) -> None:
    respx.get(f"{BASE}/api/2.1/unity-catalog/catalogs/ws_alpha").mock(
        return_value=httpx.Response(200, json={"name": "ws_alpha"})
    )
    assert await uc.catalog_exists("ws_alpha") is True


@respx.mock
async def test_catalog_exists_false_on_404(uc: UCClient) -> None:
    respx.get(f"{BASE}/api/2.1/unity-catalog/catalogs/ws_missing").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    assert await uc.catalog_exists("ws_missing") is False


@respx.mock
async def test_create_catalog_conflict_409(uc: UCClient) -> None:
    respx.post(f"{BASE}/api/2.1/unity-catalog/catalogs").mock(
        return_value=httpx.Response(409, json={"message": "already exists"})
    )
    with pytest.raises(UCConflictError):
        await uc.create_catalog("ws_alpha")


@respx.mock
async def test_create_catalog_400_with_exists_is_conflict(uc: UCClient) -> None:
    """UC OSS sometimes returns 400 with `... already exists` on dup creates."""
    respx.post(f"{BASE}/api/2.1/unity-catalog/catalogs").mock(
        return_value=httpx.Response(400, json={"message": "Catalog ws_alpha already exists."})
    )
    with pytest.raises(UCConflictError):
        await uc.create_catalog("ws_alpha")


@respx.mock
async def test_create_catalog_400_unrelated_is_bad_request(uc: UCClient) -> None:
    respx.post(f"{BASE}/api/2.1/unity-catalog/catalogs").mock(
        return_value=httpx.Response(400, json={"message": "Invalid name"})
    )
    with pytest.raises(UCBadRequestError):
        await uc.create_catalog("$$bad$$")


@respx.mock
async def test_500_is_server_error(uc: UCClient) -> None:
    respx.get(f"{BASE}/api/2.1/unity-catalog/catalogs/ws_alpha").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(UCServerError):
        await uc.get_catalog("ws_alpha")


@respx.mock
async def test_create_schema(uc: UCClient) -> None:
    respx.post(f"{BASE}/api/2.1/unity-catalog/schemas").mock(
        return_value=httpx.Response(200, json={"name": "main", "catalog_name": "ws_alpha"})
    )
    sc = await uc.create_schema("ws_alpha", "main")
    assert sc.name == "main"
    assert sc.catalog_name == "ws_alpha"


@respx.mock
async def test_list_schemas(uc: UCClient) -> None:
    respx.get(f"{BASE}/api/2.1/unity-catalog/schemas").mock(
        return_value=httpx.Response(
            200,
            json={
                "schemas": [
                    {"name": "main", "catalog_name": "ws_alpha"},
                    {"name": "raw", "catalog_name": "ws_alpha"},
                ]
            },
        )
    )
    result = await uc.list_schemas("ws_alpha")
    assert [s.name for s in result] == ["main", "raw"]


@respx.mock
async def test_list_schemas_empty(uc: UCClient) -> None:
    respx.get(f"{BASE}/api/2.1/unity-catalog/schemas").mock(
        return_value=httpx.Response(200, json={})  # UC returns no key when empty
    )
    assert await uc.list_schemas("ws_alpha") == []


@respx.mock
async def test_list_tables(uc: UCClient) -> None:
    respx.get(f"{BASE}/api/2.1/unity-catalog/tables").mock(
        return_value=httpx.Response(
            200,
            json={
                "tables": [
                    {
                        "name": "events",
                        "catalog_name": "ws_alpha",
                        "schema_name": "main",
                        "table_type": "MANAGED",
                        "data_source_format": "DELTA",
                    }
                ]
            },
        )
    )
    result = await uc.list_tables("ws_alpha", "main")
    assert result[0].name == "events"
    assert result[0].table_type == "MANAGED"


@respx.mock
async def test_get_table(uc: UCClient) -> None:
    respx.get(f"{BASE}/api/2.1/unity-catalog/tables/ws_alpha.main.events").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "events",
                "catalog_name": "ws_alpha",
                "schema_name": "main",
                "table_type": "MANAGED",
                "data_source_format": "DELTA",
                "table_id": "abc-123",
            },
        )
    )
    table = await uc.get_table("ws_alpha", "main", "events")
    assert table.table_id == "abc-123"


@respx.mock
async def test_create_table_sets_catalog_managed_property(uc: UCClient) -> None:
    route = respx.post(f"{BASE}/api/2.1/unity-catalog/tables").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "events",
                "catalog_name": "ws_alpha",
                "schema_name": "main",
                "table_type": "MANAGED",
                "data_source_format": "DELTA",
                "storage_location": "file:///tmp/events/",
                "properties": {"delta.feature.catalogManaged": "supported"},
            },
        )
    )
    table = await uc.create_table(
        catalog="ws_alpha",
        schema="main",
        name="events",
        columns=[
            {
                "name": "id",
                "type_text": "int",
                "type_name": "INT",
                "type_json": "",
                "position": 0,
                "nullable": False,
            }
        ],
        storage_location="file:///tmp/events/",
        properties={"delta.feature.catalogManaged": "supported"},
    )
    assert table.properties is not None
    assert table.properties["delta.feature.catalogManaged"] == "supported"
    # Verify the property was sent on the wire.
    sent_body = route.calls[0].request.content
    assert b"delta.feature.catalogManaged" in sent_body


@respx.mock
async def test_delete_table(uc: UCClient) -> None:
    respx.delete(f"{BASE}/api/2.1/unity-catalog/tables/ws_alpha.main.events").mock(
        return_value=httpx.Response(200, json={})
    )
    await uc.delete_table("ws_alpha", "main", "events")


@respx.mock
async def test_delete_catalog_forces(uc: UCClient) -> None:
    route = respx.delete(f"{BASE}/api/2.1/unity-catalog/catalogs/ws_alpha").mock(
        return_value=httpx.Response(200, json={})
    )
    await uc.delete_catalog("ws_alpha", force=True)
    assert route.calls[0].request.url.params.get("force") == "true"


@respx.mock
async def test_gen_temp_creds_aws(uc: UCClient) -> None:
    respx.post(f"{BASE}/api/2.1/unity-catalog/temporary-table-credentials").mock(
        return_value=httpx.Response(
            200,
            json={
                "aws_temp_credentials": {
                    "access_key_id": "AKIA…",
                    "secret_access_key": "secret",
                    "session_token": "tok",
                    "expiration_time": "2099-01-01T00:00:00Z",
                }
            },
        )
    )
    creds = await uc.gen_temp_creds(table_id="abc-123", operation="READ_WRITE")
    assert creds.aws_temp_credentials is not None
    assert creds.aws_temp_credentials["access_key_id"] == "AKIA…"


@respx.mock
async def test_token_header_sent_when_configured() -> None:
    client = UCClient(base_url=BASE, token="my-secret-token")
    route = respx.get(f"{BASE}/api/2.1/unity-catalog/catalogs/ws_alpha").mock(
        return_value=httpx.Response(200, json={"name": "ws_alpha"})
    )
    await client.get_catalog("ws_alpha")
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer my-secret-token"
    await client.aclose()


async def test_aclose_idempotent() -> None:
    client = UCClient(base_url=BASE)
    await client.aclose()
