import json

import httpx
import pytest
import respx

from api.config import settings
from api.services.assistant.gateway import Gateway, GatewayError, _translate, metric_tags


def _gateway(client=None, **kw) -> Gateway:
    kw.setdefault("row_cap", 100)
    kw.setdefault("byte_cap", 200)
    kw.setdefault("service_account_id", "sa-1")
    return Gateway(client=client, workspace_slug="ws", **kw)


def _status_error(code: int, body) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://assistant.internal/x")
    if isinstance(body, dict):
        response = httpx.Response(code, json=body, request=request)
    else:
        response = httpx.Response(code, text=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_translate_grant_denied_403_names_object():
    err = _translate(
        _status_error(403, {"error": "grant_denied", "detail": "Not authorized (reader) on c.s.t"})
    )
    assert isinstance(err, GatewayError)
    assert "Access denied" in str(err)
    assert "Not authorized (reader) on c.s.t" in str(err)


def test_translate_sql_not_allowed_422():
    err = _translate(_status_error(422, {"error": "sql_not_allowed", "detail": "ATTACH is denied"}))
    assert "Not allowed" in str(err)
    assert "ATTACH is denied" in str(err)


def test_translate_404_is_not_found():
    err = _translate(_status_error(404, {"detail": "gone"}))
    assert "Not found" in str(err)


def test_translate_plain_text_body():
    err = _translate(_status_error(500, "internal boom"))
    assert "internal boom" in str(err)


def test_cap_bytes_truncates_large_samples():
    gw = _gateway(byte_cap=200)
    rows = [{"v": "x" * 100} for _ in range(10)]
    kept, truncated = gw._cap_bytes(rows)
    assert truncated is True
    assert 0 < len(kept) < len(rows)


def test_cap_bytes_keeps_small_samples():
    gw = _gateway(byte_cap=10_000)
    rows = [{"v": i} for i in range(5)]
    kept, truncated = gw._cap_bytes(rows)
    assert truncated is False
    assert kept == rows


@respx.mock
async def test_get_query_result_refuses_other_principals_query():
    # A query owned by a different user must not be readable via the assistant.
    respx.get("http://assistant.internal/queries/q-foreign").mock(
        return_value=httpx.Response(200, json={"id": "q-foreign", "user_id": "someone-else"})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        gw = _gateway(client=client, service_account_id="sa-1")
        with pytest.raises(GatewayError, match="only page results of queries I ran"):
            await gw.get_query_result("q-foreign", cursor=None, limit=100)


@respx.mock
async def test_get_query_result_allows_own_query():
    respx.get("http://assistant.internal/queries/q-mine").mock(
        return_value=httpx.Response(200, json={"id": "q-mine", "user_id": "sa-1"})
    )
    respx.get("http://assistant.internal/queries/q-mine/rows").mock(
        return_value=httpx.Response(
            200, json={"rows": [{"n": 1}], "columns": ["n"], "cursor": None, "total": 1}
        )
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        gw = _gateway(client=client, service_account_id="sa-1")
        page = await gw.get_query_result("q-mine", cursor=None, limit=100)
        assert page["rows"] == [{"n": 1}]


@pytest.mark.parametrize("code,prefix", [(409, "Conflict"), (503, "Service unavailable")])
def test_translate_other_codes(code, prefix):
    err = _translate(_status_error(code, {"detail": "x"}))
    assert prefix in str(err)


@respx.mock
async def test_count_agents_counts_only_the_ones_that_can_run_a_query():
    """``GET /agents`` lists registered agents, reporting disconnected ones as
    ``unavailable``. Counting rows would tell the prompt a fleet exists that
    cannot take a query — and on an elastic deployment terminated agents are kept
    for reuse, so the row count grows while the usable fleet stays at one."""
    respx.get("http://assistant.internal/agents").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "a", "status": "healthy"},
                {"id": "b", "status": "unavailable"},
                {"id": "c", "status": "healthy"},
            ],
        )
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        assert await _gateway(client=client).count_agents() == 2


# --- lineage ------------------------------------------------------------------


def _graph(**overrides) -> dict:
    """A two-node graph with one redacted upstream, as the endpoint returns it."""
    graph = {
        "root": "cat:abc:public:orders",
        "nodes": [
            {
                "key": "cat:abc:public:orders",
                "kind": "table",
                "catalog": "warehouse",
                "schema_name": "public",
                "table": "orders",
                "system": None,
                "distance": 0,
                "column_count": 3,
            },
            {
                "key": "redacted:0123456789abcdef",
                "kind": "redacted",
                "catalog": None,
                "schema_name": None,
                "table": None,
                "system": None,
                "distance": -1,
                "column_count": 0,
            },
        ],
        "edges": [
            {
                "source_key": "redacted:0123456789abcdef",
                "target_key": "cat:abc:public:orders",
                "operation": "insert",
                "confidence": "exact",
                "stale": False,
                "first_seen_at": "2026-01-01T00:00:00Z",
                "last_seen_at": "2026-09-01T00:00:00Z",
                "observation_count": 42,
                "last_query_id": "8f3b1c2e-0000-4000-8000-000000000000",
                "column_lineage": "derived",
                "columns": [
                    {"source_column": "id", "target_column": "order_id", "providers": ["dbt"]}
                ],
                "providers": [
                    {
                        "name": "dbt",
                        "first_seen_at": "2026-01-01T00:00:00Z",
                        "last_seen_at": "2026-09-01T00:00:00Z",
                        "observation_count": 40,
                        "stale": False,
                        "column_lineage": "derived",
                    },
                    {
                        "name": "duckhaven",
                        "first_seen_at": "2026-02-01T00:00:00Z",
                        "last_seen_at": "2026-03-01T00:00:00Z",
                        "observation_count": 2,
                        "stale": True,
                        "column_lineage": "unknown",
                    },
                ],
            }
        ],
        "truncated": False,
        "hidden": False,
        "columns_truncated": False,
    }
    graph.update(overrides)
    return graph


def _lineage_route(graph: dict):
    return respx.get(
        "http://assistant.internal/workspaces/ws/catalogs/warehouse"
        "/schemas/public/tables/orders/lineage"
    ).mock(return_value=httpx.Response(200, json=graph))


@respx.mock
async def test_table_lineage_keeps_a_redacted_node_without_naming_it():
    """A redacted node is real lineage the caller may not read.

    Dropping it would silently shorten the path and make a partial graph look
    complete; naming it would defeat the grant. Its key is a hash, so passing it
    through keeps the shape without leaking the identity.
    """
    _lineage_route(_graph())
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        out = await _gateway(client=client).table_lineage("warehouse", "public", "orders")

    redacted = next(n for n in out["nodes"] if n["kind"] == "redacted")
    assert redacted["key"].startswith("redacted:")
    assert not {"catalog", "schema", "table", "system"} & set(redacted)
    assert redacted["distance"] == -1
    # The edge still references it, so the graph joins up.
    assert out["edges"][0]["source_key"] == redacted["key"]


@respx.mock
async def test_table_lineage_flattens_providers_to_names():
    """Per-producer freshness is most of the payload and answers a human's
    question, not an agent's. Which producer claimed the edge is the part that
    changes what an agent says, so the names stay."""
    _lineage_route(_graph())
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        out = await _gateway(client=client).table_lineage("warehouse", "public", "orders")

    edge = out["edges"][0]
    assert edge["providers"] == ["dbt", "duckhaven"]
    assert edge["operation"] == "insert"
    assert edge["column_lineage"] == "derived"
    assert edge["columns"] == [{"source_column": "id", "target_column": "order_id"}]
    # Dropped: the timestamp/count block, and a query id the agent cannot use.
    assert "first_seen_at" not in edge
    assert "observation_count" not in edge
    assert "last_query_id" not in edge


@respx.mock
@pytest.mark.parametrize("flag", ["truncated", "hidden", "columns_truncated"])
async def test_table_lineage_surfaces_every_truncation_flag(flag):
    """A partial graph that reads as complete is the one failure worth avoiding.

    "Nothing depends on this table" is a conclusion someone acts on, so each cap
    has to reach the caller rather than being flattened away by the trim.
    """
    _lineage_route(_graph(**{flag: True}))
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        out = await _gateway(client=client).table_lineage("warehouse", "public", "orders")
    assert out[flag] is True


@respx.mock
async def test_table_lineage_forwards_its_walk_parameters():
    route = _lineage_route(_graph())
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        await _gateway(client=client).table_lineage(
            "warehouse", "public", "orders", direction="upstream", depth=4, columns_for=["k1"]
        )
    query = route.calls.last.request.url.params
    assert query["direction"] == "upstream"
    assert query["depth"] == "4"
    assert query["columns_for"] == "k1"


@respx.mock
async def test_table_lineage_omits_columns_for_when_not_asked():
    """Column detail is bounded by table width, not graph size, so it stays opt-in."""
    route = _lineage_route(_graph())
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        await _gateway(client=client).table_lineage("warehouse", "public", "orders")
    assert "columns_for" not in route.calls.last.request.url.params


# --- SQL execution ------------------------------------------------------------


@respx.mock
async def test_run_sql_lets_the_server_choose_the_agent():
    """It used to take the first healthy agent from any workspace — possibly one
    that can't serve this one's catalogs, or a beta runtime. The server's own
    choice checks both."""
    submitted = respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(202, json={"id": "q1", "status": "done"})
    )
    respx.get("http://assistant.internal/queries/q1/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        await _gateway(client=client).run_sql("SELECT 1", catalog=None, timeout_s=5)

    body = submitted.calls.last.request.content
    assert b"agent_id" not in body


def test_translate_says_plainly_when_no_agent_can_run_sql():
    err = _translate(
        _status_error(
            422, {"detail": {"error": "agent_required", "detail": "agent_id is required"}}
        )
    )
    assert str(err) == "No compute agent is currently available to run SQL."


@respx.mock
async def test_run_sql_waits_on_the_server_instead_of_polling():
    """A query that finishes inside the wait costs one call, not a stream of polls."""
    submitted = respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(200, json={"id": "q1", "status": "done"})
    )
    polled = respx.get("http://assistant.internal/queries/q1")
    respx.get("http://assistant.internal/queries/q1/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        await _gateway(client=client).run_sql("SELECT 1", catalog=None, timeout_s=5)

    assert json.loads(submitted.calls.last.request.content)["wait_timeout_s"] == 5
    assert not polled.called


@respx.mock
async def test_run_sql_caps_the_wait_and_polls_past_it():
    submitted = respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(202, json={"id": "q1", "status": "running"})
    )
    polled = respx.get("http://assistant.internal/queries/q1").mock(
        return_value=httpx.Response(200, json={"id": "q1", "status": "done"})
    )
    respx.get("http://assistant.internal/queries/q1/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        page = await _gateway(client=client).run_sql("SELECT 1", catalog=None, timeout_s=600)

    sent = json.loads(submitted.calls.last.request.content)["wait_timeout_s"]
    assert sent == settings.sql_statement_max_wait_timeout_s
    assert polled.called
    assert page["status"] == "done"


@respx.mock
async def test_run_sql_sends_the_gateways_tags_and_the_calls_own():
    submitted = respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(200, json={"id": "q1", "status": "done"})
    )
    respx.get("http://assistant.internal/queries/q1/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        gw = _gateway(client=client, tags={"assistant.conversation": "c-1"})
        await gw.run_sql("SELECT 1", catalog=None, timeout_s=5, tags={"semantic.model": "sales"})
        await _gateway(client=client).run_sql("SELECT 1", catalog=None, timeout_s=5)

    first, second = (json.loads(c.request.content) for c in submitted.calls)
    assert first["tags"] == {"assistant.conversation": "c-1", "semantic.model": "sales"}
    assert "tags" not in second


@respx.mock
async def test_a_result_names_its_column_types():
    """Exact numbers arrive as strings; the types say which strings are numbers."""
    respx.get("http://assistant.internal/queries/q-mine").mock(
        return_value=httpx.Response(200, json={"id": "q-mine", "user_id": "sa-1"})
    )
    respx.get("http://assistant.internal/queries/q-mine/rows").mock(
        return_value=httpx.Response(
            200,
            json={
                "rows": [{"n": "2", "label": "x"}],
                "columns": ["n", "label"],
                "column_schema": [
                    {"name": "n", "type": "BIGINT"},
                    {"name": "label", "type": "VARCHAR"},
                ],
                "cursor": None,
                "total": 1,
            },
        )
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        page = await _gateway(client=client).get_query_result("q-mine", cursor=None, limit=10)

    assert page["column_types"] == {"n": "BIGINT", "label": "VARCHAR"}


@respx.mock
async def test_a_result_without_a_schema_has_no_column_types():
    respx.get("http://assistant.internal/queries/q-mine").mock(
        return_value=httpx.Response(200, json={"id": "q-mine", "user_id": "sa-1"})
    )
    respx.get("http://assistant.internal/queries/q-mine/rows").mock(
        return_value=httpx.Response(
            200, json={"rows": [{"n": 1}], "columns": ["n"], "cursor": None, "total": 1}
        )
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        page = await _gateway(client=client).get_query_result("q-mine", cursor=None, limit=10)

    assert "column_types" not in page


def test_metric_tags_fit_the_servers_value_limit():
    tags = metric_tags("sales", [f"metric_{i}" for i in range(100)])

    assert tags["semantic.model"] == "sales"
    assert tags["semantic.metrics"].startswith("metric_0,metric_1,")
    assert len(tags["semantic.metrics"]) == 256
