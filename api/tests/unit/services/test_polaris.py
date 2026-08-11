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


# The five idempotent writes ensure_catalog_access makes: two role-create POSTs
# (idempotent via _create's 409 handling) and three privilege-grant + two
# role-binding PUTs (idempotent via _put's duplicate-key handling).
_GRANT_PUT = f"{MGMT}/catalogs/ws_alpha/catalog-roles/duckhaven_rw/grants"
_ROLE_BIND_PUT = f"{MGMT}/principal-roles/duckhaven/catalog-roles/ws_alpha"
_PRINCIPAL_BIND_PUT = f"{MGMT}/principals/root/principal-roles"

# Polaris returns this (wrapped) Postgres error body, with HTTP 500, when a
# grant_records row already exists — for both privilege grants and role bindings.
_DUP_KEY_BODY = (
    "Failed to write to grant records due to Failed due to 'ERROR: duplicate key "
    "value violates unique constraint \"grant_records_pkey\" ...' (sql-state '23505')"
)


def _mock_access_writes(failing_put: str) -> None:
    """Mock ensure_catalog_access's writes as 2xx — the two role-create POSTs and
    every PUT except `failing_put`, which the caller mocks to fail (one route per
    URL, so respx route precedence is unambiguous)."""
    respx.post(f"{MGMT}/catalogs/ws_alpha/catalog-roles").mock(return_value=httpx.Response(201))
    respx.post(f"{MGMT}/principal-roles").mock(return_value=httpx.Response(201))
    for url in (_GRANT_PUT, _ROLE_BIND_PUT, _PRINCIPAL_BIND_PUT):
        if url != failing_put:
            respx.put(url).mock(return_value=httpx.Response(201))


@pytest.mark.parametrize("failing_put", [_GRANT_PUT, _ROLE_BIND_PUT, _PRINCIPAL_BIND_PUT])
@respx.mock
async def test_ensure_catalog_access_tolerates_duplicate_key(
    polaris: PolarisClient, failing_put: str
) -> None:
    """Every PUT (privilege grant and role binding alike) routes through the
    duplicate-key-tolerant _put: a 500 with a Postgres duplicate-key body — Polaris's
    non-idempotent response when the grant_records row exists — must be a no-op."""
    _mock_token()
    _mock_access_writes(failing_put)
    respx.put(failing_put).mock(return_value=httpx.Response(500, text=_DUP_KEY_BODY))
    # Must not raise.
    await polaris.ensure_catalog_access("ws_alpha")


@respx.mock
async def test_ensure_catalog_access_reraises_real_server_error(polaris: PolarisClient) -> None:
    """A 500 whose body is not a duplicate-key violation is a real failure."""
    _mock_token()
    _mock_access_writes(_GRANT_PUT)
    respx.put(_GRANT_PUT).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(PolarisServerError):
        await polaris.ensure_catalog_access("ws_alpha")


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
async def test_get_table_reads_current_snapshot_summary(polaris: PolarisClient) -> None:
    """`current_snapshot_summary` comes from the same LoadTableResult payload
    `get_table` already fetches — no extra request."""
    _mock_token()
    respx.get(f"{CAT}/ws_alpha/namespaces/main/tables/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "metadata": {
                    "table-uuid": "abc-123",
                    "current-snapshot-id": 22,
                    "snapshots": [
                        {
                            "snapshot-id": 11,
                            "timestamp-ms": 1000,
                            "summary": {"operation": "append", "total-records": "5"},
                        },
                        {
                            "snapshot-id": 22,
                            "parent-snapshot-id": 11,
                            "timestamp-ms": 2000,
                            "summary": {"operation": "overwrite", "total-records": "8"},
                        },
                    ],
                },
            },
        )
    )
    t = await polaris.get_table("ws_alpha", "main", "events")
    assert t.current_snapshot_summary == {"operation": "overwrite", "total-records": "8"}


@respx.mock
async def test_get_table_has_no_snapshot_summary_when_no_snapshots(polaris: PolarisClient) -> None:
    _mock_token()
    respx.get(f"{CAT}/ws_alpha/namespaces/main/tables/fresh").mock(
        return_value=httpx.Response(200, json={"metadata": {"table-uuid": "x"}})
    )
    t = await polaris.get_table("ws_alpha", "main", "fresh")
    assert t.current_snapshot_summary is None


@respx.mock
async def test_list_snapshots_orders_newest_first_and_flags_current(
    polaris: PolarisClient,
) -> None:
    _mock_token()
    respx.get(f"{CAT}/ws_alpha/namespaces/main/tables/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "metadata": {
                    "current-snapshot-id": 22,
                    "snapshots": [
                        {
                            "snapshot-id": 11,
                            "timestamp-ms": 1000,
                            "schema-id": 0,
                            "summary": {"operation": "append", "added-records": "5"},
                        },
                        {
                            "snapshot-id": 22,
                            "parent-snapshot-id": 11,
                            "timestamp-ms": 2000,
                            "schema-id": 0,
                            "summary": {"operation": "overwrite", "added-records": "3"},
                        },
                    ],
                },
            },
        )
    )
    snaps = await polaris.list_snapshots("ws_alpha", "main", "events")
    # Newest first.
    assert [s.snapshot_id for s in snaps] == [22, 11]
    assert snaps[0].is_current is True
    assert snaps[0].parent_snapshot_id == 11
    assert snaps[0].operation == "overwrite"
    assert snaps[1].is_current is False


@respx.mock
async def test_list_snapshots_empty_when_no_snapshot_log(polaris: PolarisClient) -> None:
    _mock_token()
    respx.get(f"{CAT}/ws_alpha/namespaces/main/tables/fresh").mock(
        return_value=httpx.Response(200, json={"metadata": {"table-uuid": "x"}})
    )
    assert await polaris.list_snapshots("ws_alpha", "main", "fresh") == []


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
async def test_delete_table_defaults_to_no_purge(polaris: PolarisClient) -> None:
    _mock_token()
    route = respx.delete(f"{CAT}/ws_alpha/namespaces/main/tables/events").mock(
        return_value=httpx.Response(204)
    )
    await polaris.delete_table("ws_alpha", "main", "events")
    assert route.called
    assert "purgeRequested" not in str(route.calls.last.request.url)


@respx.mock
async def test_delete_table_with_purge(polaris: PolarisClient) -> None:
    _mock_token()
    route = respx.delete(f"{CAT}/ws_alpha/namespaces/main/tables/events").mock(
        return_value=httpx.Response(204)
    )
    await polaris.delete_table("ws_alpha", "main", "events", purge=True)
    assert route.called
    assert "purgeRequested=true" in str(route.calls.last.request.url)


@respx.mock
async def test_reauths_and_retries_once_on_401(polaris: PolarisClient) -> None:
    """A cached token can outlive its wall-clock exp across a host suspend/resume
    (monotonic time pauses), so Polaris starts returning 401 while our refresh
    timer still considers the token fresh. The client must drop the token,
    re-mint, and replay the request once with the fresh token."""
    token_route = respx.post(f"{CAT}/oauth/tokens").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "stale", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600}),
        ]
    )

    def resource(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") == "Bearer stale":
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"namespaces": [["analytics"]]})

    ns_route = respx.get(f"{CAT}/new/namespaces").mock(side_effect=resource)

    names = [s.name for s in await polaris.list_schemas("new")]

    assert names == ["analytics"]
    # Initial mint + one reactive re-auth; the resource is hit twice (401 then
    # the successful retry with the fresh token).
    assert token_route.call_count == 2
    assert ns_route.call_count == 2
    assert ns_route.calls.last.request.headers["Authorization"] == "Bearer fresh"


@respx.mock
async def test_persistent_401_retries_once_then_raises(polaris: PolarisClient) -> None:
    """If a freshly minted token is *also* rejected, the client retries exactly
    once (no infinite loop) and surfaces the 401 as a BadRequest error."""
    token_route = respx.post(f"{CAT}/oauth/tokens").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "t1", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "t2", "expires_in": 3600}),
        ]
    )
    ns_route = respx.get(f"{CAT}/new/namespaces").mock(return_value=httpx.Response(401, json={}))

    with pytest.raises(PolarisBadRequestError):
        await polaris.list_schemas("new")

    # Exactly one re-auth and one replay — the retry does not recurse.
    assert token_route.call_count == 2
    assert ns_route.call_count == 2


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
