"""Unit tests for `api.services.polaris.PolarisClient`.

Uses respx to mock the underlying httpx transport. Every authenticated
call first exchanges an OAuth2 token, so each test mocks the token
endpoint. Covers the happy path for the methods production code calls,
plus the status-code dispatch (404 → NotFound, 409 → Conflict, other 4xx
→ BadRequest, 5xx → ServerError).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from api.services.polaris import (
    PolarisBadRequestError,
    PolarisClient,
    PolarisConflictError,
    PolarisNotFoundError,
    PolarisServerError,
)

BASE = "http://polaris.test"
CAT = f"{BASE}/api/catalog/v1"
MGMT = f"{BASE}/api/management/v1"


@pytest.fixture
async def polaris() -> PolarisClient:
    return PolarisClient(
        base_url=BASE,
        realm="POLARIS",
        client_id="root",
        client_secret="s3cr3t",
        timeout_s=1.0,
    )


def _mock_token() -> None:
    respx.post(f"{CAT}/oauth/tokens").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )


@respx.mock
async def test_token_is_cached_and_sent(polaris: PolarisClient) -> None:
    token_route = respx.post(f"{CAT}/oauth/tokens").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    cat_route = respx.get(f"{MGMT}/catalogs/ws_alpha").mock(
        return_value=httpx.Response(200, json={"name": "ws_alpha"})
    )
    await polaris.get_catalog("ws_alpha")
    await polaris.get_catalog("ws_alpha")
    # Token fetched once, reused for both calls; Bearer header attached.
    assert token_route.call_count == 1
    assert cat_route.calls.last.request.headers["Authorization"] == "Bearer tok"


@respx.mock
async def test_create_catalog_sends_storage_config(polaris: PolarisClient) -> None:
    _mock_token()
    route = respx.post(f"{MGMT}/catalogs").mock(return_value=httpx.Response(201))
    cat = await polaris.create_catalog(
        "ws_alpha",
        storage_type="S3",
        base_location="s3://warehouse/ws_alpha",
        extra_storage={"endpoint": "http://minio:9000", "pathStyleAccess": True},
    )
    assert cat.name == "ws_alpha"
    sent = route.calls.last.request.content
    assert b'"storageType":"S3"' in sent.replace(b" ", b"")
    assert b"s3://warehouse/ws_alpha" in sent
    assert b"http://minio:9000" in sent
    # Catalogs are created DuckHaven-owned with drop-with-purge enabled.
    assert b"polaris.config.drop-with-purge.enabled" in sent


@respx.mock
async def test_ensure_catalog_access_grants_full_ownership(polaris: PolarisClient) -> None:
    """ensure_catalog_access wires the full catalog-management privilege set and
    binds the RW role to the service principal."""
    _mock_token()
    grants = respx.put(f"{MGMT}/catalogs/ws_alpha/catalog-roles/duckhaven_rw/grants").mock(
        return_value=httpx.Response(201)
    )
    respx.post(f"{MGMT}/catalogs/ws_alpha/catalog-roles").mock(return_value=httpx.Response(201))
    respx.post(f"{MGMT}/principal-roles").mock(return_value=httpx.Response(201))
    respx.put(f"{MGMT}/principal-roles/duckhaven/catalog-roles/ws_alpha").mock(
        return_value=httpx.Response(201)
    )
    respx.put(f"{MGMT}/principals/root/principal-roles").mock(return_value=httpx.Response(201))

    await polaris.ensure_catalog_access("ws_alpha")

    granted = {json.loads(call.request.content)["grant"]["privilege"] for call in grants.calls}
    assert granted == {
        "CATALOG_MANAGE_CONTENT",
        "CATALOG_MANAGE_METADATA",
        "CATALOG_MANAGE_ACCESS",
    }


@respx.mock
async def test_catalog_exists_true_false(polaris: PolarisClient) -> None:
    _mock_token()
    respx.get(f"{MGMT}/catalogs/yes").mock(return_value=httpx.Response(200, json={"name": "yes"}))
    respx.get(f"{MGMT}/catalogs/no").mock(return_value=httpx.Response(404, json={}))
    assert await polaris.catalog_exists("yes") is True
    assert await polaris.catalog_exists("no") is False


@respx.mock
async def test_create_schema_and_list(polaris: PolarisClient) -> None:
    _mock_token()
    respx.post(f"{CAT}/ws_alpha/namespaces").mock(return_value=httpx.Response(200))
    sc = await polaris.create_schema("ws_alpha", "main")
    assert sc.name == "main"

    respx.get(f"{CAT}/ws_alpha/namespaces").mock(
        return_value=httpx.Response(200, json={"namespaces": [["main"], ["staging"]]})
    )
    names = [s.name for s in await polaris.list_schemas("ws_alpha")]
    assert names == ["main", "staging"]


@respx.mock
async def test_list_tables_returns_identifiers(polaris: PolarisClient) -> None:
    _mock_token()
    respx.get(f"{CAT}/ws_alpha/namespaces/main/tables").mock(
        return_value=httpx.Response(
            200,
            json={"identifiers": [{"namespace": ["main"], "name": "events"}]},
        )
    )
    tables = await polaris.list_tables("ws_alpha", "main")
    assert [t.name for t in tables] == ["events"]
    assert tables[0].data_source_format == "ICEBERG"
    assert tables[0].columns == []


@respx.mock
async def test_get_table_maps_columns(polaris: PolarisClient) -> None:
    _mock_token()
    respx.get(f"{CAT}/ws_alpha/namespaces/main/tables/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "metadata-location": "file:///w/ws_alpha/main/events/metadata/v1.json",
                "metadata": {
                    "table-uuid": "abc-123",
                    "current-schema-id": 0,
                    "schemas": [
                        {
                            "schema-id": 0,
                            "fields": [
                                {"id": 1, "name": "id", "required": True, "type": "long"},
                                {"id": 2, "name": "label", "required": False, "type": "string"},
                            ],
                        }
                    ],
                    "properties": {"k": "v"},
                },
            },
        )
    )
    t = await polaris.get_table("ws_alpha", "main", "events")
    assert t.table_id == "abc-123"
    assert t.storage_location.endswith("v1.json")
    assert [(c.name, c.type_name, c.nullable) for c in t.columns] == [
        ("id", "LONG", False),
        ("label", "STRING", True),
    ]


@respx.mock
async def test_create_table_posts_schema(polaris: PolarisClient) -> None:
    _mock_token()
    route = respx.post(f"{CAT}/ws_alpha/namespaces/main/tables").mock(
        return_value=httpx.Response(
            200,
            json={
                "metadata": {
                    "table-uuid": "t-1",
                    "current-schema-id": 0,
                    "schemas": [
                        {"schema-id": 0, "fields": [{"id": 1, "name": "id", "type": "int"}]}
                    ],
                }
            },
        )
    )
    t = await polaris.create_table(
        catalog="ws_alpha",
        schema="main",
        name="events",
        columns=[{"id": 1, "name": "id", "required": False, "type": "int"}],
    )
    assert t.table_id == "t-1"
    assert [c.name for c in t.columns] == ["id"]
    assert b'"type":"struct"' in route.calls.last.request.content.replace(b" ", b"")


@respx.mock
async def test_delete_table(polaris: PolarisClient) -> None:
    _mock_token()
    route = respx.delete(f"{CAT}/ws_alpha/namespaces/main/tables/events").mock(
        return_value=httpx.Response(204)
    )
    await polaris.delete_table("ws_alpha", "main", "events")
    assert route.called
    assert "purgeRequested=true" in str(route.calls.last.request.url)


@respx.mock
async def test_status_dispatch(polaris: PolarisClient) -> None:
    _mock_token()
    respx.get(f"{MGMT}/catalogs/c404").mock(return_value=httpx.Response(404, json={}))
    respx.get(f"{MGMT}/catalogs/c409").mock(
        return_value=httpx.Response(409, json={"error": {"message": "exists"}})
    )
    respx.get(f"{MGMT}/catalogs/c400").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad"}})
    )
    respx.get(f"{MGMT}/catalogs/c500").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(PolarisNotFoundError):
        await polaris.get_catalog("c404")
    with pytest.raises(PolarisConflictError):
        await polaris.get_catalog("c409")
    with pytest.raises(PolarisBadRequestError):
        await polaris.get_catalog("c400")
    with pytest.raises(PolarisServerError):
        await polaris.get_catalog("c500")
