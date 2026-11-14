import asyncio
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
async def test_workspace_compute_reads_the_default_from_the_workspace():
    """Not from ``GET /agents``: that list is filtered by the service account's own
    agent grants, so it says nothing about which agent a query will be sent to."""
    respx.get("http://assistant.internal/workspaces/ws").mock(
        return_value=httpx.Response(
            200,
            json={
                "slug": "ws",
                "default_agent": {"id": "a", "name": "analytics", "provider": "docker"},
            },
        )
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        assert await _gateway(client=client).workspace_compute() == {
            "name": "analytics",
            "elastic": True,
        }


@respx.mock
async def test_workspace_compute_marks_a_static_default_as_not_elastic():
    respx.get("http://assistant.internal/workspaces/ws").mock(
        return_value=httpx.Response(
            200, json={"default_agent": {"id": "a", "name": "ops", "provider": None}}
        )
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        assert await _gateway(client=client).workspace_compute() == {
            "name": "ops",
            "elastic": False,
        }


@respx.mock
async def test_workspace_compute_without_a_default_says_so():
    respx.get("http://assistant.internal/workspaces/ws").mock(
        return_value=httpx.Response(200, json={"slug": "ws", "default_agent": None})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        assert await _gateway(client=client).workspace_compute() == {
            "name": None,
            "elastic": False,
        }


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


def test_translate_reads_the_apis_error_envelope():
    """The API answers ``{"error", "message", "details"}``. Reading only the older
    ``detail`` shapes would hand the model the bare code instead of the sentence."""
    err = _translate(
        _status_error(
            403,
            {
                "error": "grant_denied",
                "message": "Not authorized (reader) on c.s.t",
                "details": None,
            },
        )
    )
    assert "Not authorized (reader) on c.s.t" in str(err)


def test_translate_tells_the_model_it_cannot_start_compute():
    err = _translate(
        _status_error(
            503,
            {"error": "no_compute_available", "message": "No compute is running.", "details": None},
        )
    )
    assert "cannot start any" in str(err)
    assert "default compute" in str(err)


def test_translate_names_the_default_compute_that_is_unavailable():
    err = _translate(
        _status_error(
            503,
            {
                "error": "default_compute_unavailable",
                "message": "Default compute 'ops' is not connected",
                "details": {"agent_name": "ops", "reason": "disconnected"},
            },
        )
    )
    assert "Default compute 'ops' is not connected" in str(err)
    assert "cannot choose a different engine" in str(err)


def test_translate_a_full_queue_asks_the_user_to_retry():
    err = _translate(
        _status_error(
            429, {"error": "compute_queue_full", "message": "queue is full", "details": None}
        )
    )
    assert "try again shortly" in str(err)


@respx.mock
async def test_run_sql_never_asks_the_server_to_add_an_agent():
    submitted = respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(202, json={"id": "q1", "status": "done"})
    )
    respx.get("http://assistant.internal/queries/q1/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        await _gateway(client=client).run_sql("SELECT 1", catalog=None, timeout_s=5)

    assert json.loads(submitted.calls.last.request.content)["scale_out"] is False


@respx.mock
async def test_a_cold_start_does_not_eat_the_querys_own_timeout(monkeypatch):
    """The agent starts the execution clock only once the run is bound, so the wait
    for compute is budgeted on top of ``timeout_s``, not inside it."""
    monkeypatch.setattr("api.services.assistant.gateway._POLL_INTERVAL_S", 0.01)
    respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(202, json={"id": "q1", "status": "queued"})
    )
    states = iter(["queued"] * 12 + ["done"])
    respx.get("http://assistant.internal/queries/q1").mock(
        side_effect=lambda request: httpx.Response(200, json={"id": "q1", "status": next(states)})
    )
    respx.get("http://assistant.internal/queries/q1/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        page = await _gateway(client=client, queue_wait_s=5.0).run_sql(
            "SELECT 1", catalog=None, timeout_s=0.05
        )
    assert page["status"] == "done"


@respx.mock
async def test_without_a_queue_wait_the_query_times_out_at_its_own_budget(monkeypatch):
    monkeypatch.setattr("api.services.assistant.gateway._POLL_INTERVAL_S", 0.01)
    respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(202, json={"id": "q1", "status": "queued"})
    )
    respx.get("http://assistant.internal/queries/q1").mock(
        return_value=httpx.Response(200, json={"id": "q1", "status": "queued"})
    )
    respx.delete("http://assistant.internal/queries/q1").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        with pytest.raises(GatewayError, match="timed out"):
            await _gateway(client=client, queue_wait_s=0.0).run_sql(
                "SELECT 1", catalog=None, timeout_s=0.05
            )


@respx.mock
async def test_a_run_that_waited_for_compute_says_how_long(monkeypatch):
    monkeypatch.setattr("api.services.assistant.gateway._POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr("api.services.assistant.gateway._WAITED_NOTE_AFTER_S", 0.0)
    respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(202, json={"id": "q1", "status": "queued"})
    )
    respx.get("http://assistant.internal/queries/q1").mock(
        return_value=httpx.Response(200, json={"id": "q1", "status": "done"})
    )
    respx.get("http://assistant.internal/queries/q1/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        page = await _gateway(client=client).run_sql("SELECT 1", catalog=None, timeout_s=5)
    assert "queued_for_s" in page


@respx.mock
async def test_a_run_that_started_at_once_reports_no_wait():
    respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(202, json={"id": "q1", "status": "done"})
    )
    respx.get("http://assistant.internal/queries/q1/rows").mock(
        return_value=httpx.Response(200, json={"columns": [], "rows": [], "total": 0})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        page = await _gateway(client=client).run_sql("SELECT 1", catalog=None, timeout_s=5)
    assert "queued_for_s" not in page


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


@pytest.mark.parametrize(
    ("code", "body", "kind"),
    [
        (403, {"error": "grant_denied", "message": "Not authorized", "details": None}, "denied"),
        (
            404,
            {"error": "not_found", "message": "Table x does not exist", "details": None},
            "denied",
        ),
        (
            422,
            {"error": "sql_not_allowed", "message": "ATTACH is denied", "details": None},
            "denied",
        ),
        (422, {"error": "statement_not_allowed", "message": "no", "details": None}, "denied"),
        (
            409,
            {"error": "catalog_read_only", "message": "read-only window", "details": None},
            "denied",
        ),
        (
            422,
            {"error": "agent_required", "message": "agent_id is required", "details": None},
            "error",
        ),
        (503, {"error": "no_compute_available", "message": "none", "details": None}, "error"),
        (
            503,
            {"error": "default_compute_unavailable", "message": "stopped", "details": None},
            "error",
        ),
        (429, {"error": "compute_queue_full", "message": "full", "details": None}, "error"),
        (503, {"error": "unavailable", "message": "Agent not connected", "details": None}, "error"),
        (409, {"error": "conflict", "message": "name taken", "details": None}, "error"),
        (500, {"error": "internal_error", "message": "boom", "details": None}, "error"),
    ],
)
def test_translate_classifies_a_refusal_apart_from_a_failure(code, body, kind):
    """A compute outage is not the platform saying no, and the audit trail must not
    record it as if it were."""
    assert _translate(_status_error(code, body)).kind == kind


def test_a_gateway_error_is_a_failure_unless_it_says_otherwise():
    assert GatewayError("something broke").kind == "error"
    assert GatewayError("no", kind="denied").kind == "denied"
    assert str(GatewayError("something broke")) == "something broke"


@respx.mock
async def test_paging_someone_elses_query_is_a_refusal():
    respx.get("http://assistant.internal/queries/q1").mock(
        return_value=httpx.Response(200, json={"id": "q1", "user_id": "someone-else"})
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        with pytest.raises(GatewayError) as exc:
            await _gateway(client=client).get_query_result("q1", cursor=None, limit=10)
    assert exc.value.kind == "denied"


@respx.mock
async def test_a_query_that_fails_is_a_failure_not_a_refusal():
    respx.post("http://assistant.internal/workspaces/ws/queries").mock(
        return_value=httpx.Response(
            202, json={"id": "q1", "status": "failed", "error": "Binder Error"}
        )
    )
    async with httpx.AsyncClient(base_url="http://assistant.internal") as client:
        with pytest.raises(GatewayError) as exc:
            await _gateway(client=client).run_sql("SELECT x", catalog=None, timeout_s=5)
    assert exc.value.kind == "error"


# ── Dashboards ────────────────────────────────────────────────────────────────

BASE = "http://assistant.internal"


def _sse(*events: dict) -> str:
    return ": ping\n\n" + "".join(f"data: {json.dumps(e)}\n\n" for e in events)


@respx.mock
async def test_listing_dashboards_keeps_what_tells_them_apart():
    route = respx.get(f"{BASE}/workspaces/ws/dashboards").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"name": "sales", "title": "Sales", "spec": {"rows": []}, "labels": ["x"]}
                ],
                "has_more": True,
            },
        )
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        out = await _gateway(client=client).list_dashboards(query="sal", uses="orders.revenue")

    assert route.calls[0].request.url.params["q"] == "sal"
    assert route.calls[0].request.url.params["uses"] == "orders.revenue"
    assert out["more"] is True
    assert out["dashboards"][0]["name"] == "sales" and "spec" not in out["dashboards"][0]


@respx.mock
async def test_a_dashboard_too_long_to_read_keeps_its_layout_and_cuts_its_sql():
    widgets = [
        {"id": f"w{i}", "type": "table", "title": f"W{i}", "sql": "x" * 3000} for i in range(3)
    ]
    route = respx.get(f"{BASE}/workspaces/ws/dashboards/sales").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "sales",
                "title": "Sales",
                "version": 4,
                "spec": {"rows": [{"widgets": widgets}]},
                "refs": [{"kind": "table", "name": "c.s.t"}],
                "warnings": [{"message": "16 widgets is a lot."}],
            },
        )
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        out = await _gateway(client=client, byte_cap=2000).get_dashboard("sales", draft=True)

    assert route.calls[0].request.url.params["draft"] == "true"
    assert out["version"] == 4 and out["warnings"] == ["16 widgets is a lot."]
    kept = out["spec"]["rows"][0]["widgets"]
    assert [w["id"] for w in kept] == ["w0", "w1", "w2"]
    assert all(len(w["sql"]) <= 501 for w in kept)
    assert "cut short" in out["truncated"]


@respx.mock
async def test_running_a_dashboard_reads_its_events_to_the_end():
    route = respx.post(f"{BASE}/workspaces/ws/dashboards/sales/run").mock(
        return_value=httpx.Response(
            200,
            text=_sse({"type": "start"}, {"type": "widget", "id": "revenue"}, {"type": "done"}),
            headers={"content-type": "text/event-stream"},
        )
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        events = await _gateway(client=client, queue_wait_s=0).run_dashboard(
            "sales",
            params={"country": "NL"},
            widgets=["revenue"],
            draft=False,
            compare="previous_year",
            timeout_s=5,
        )

    assert [e["type"] for e in events] == ["start", "widget", "done"]
    assert json.loads(route.calls[0].request.content) == {
        "params": {"country": "NL"},
        "widgets": ["revenue"],
        "draft": False,
        "compare": "previous_year",
    }


@respx.mock
async def test_a_run_without_a_comparison_sends_none():
    route = respx.post(f"{BASE}/workspaces/ws/dashboards/sales/run").mock(
        return_value=httpx.Response(200, text=_sse({"type": "done"}))
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        await _gateway(client=client, queue_wait_s=0).run_dashboard(
            "sales", params=None, widgets=None, draft=True, compare=None, timeout_s=5
        )
    assert "compare" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_a_run_that_fails_says_why():
    respx.post(f"{BASE}/workspaces/ws/dashboards/sales/run").mock(
        return_value=httpx.Response(
            200, text=_sse({"type": "start"}, {"type": "error", "message": "No compute."})
        )
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        with pytest.raises(GatewayError, match="No compute."):
            await _gateway(client=client, queue_wait_s=0).run_dashboard(
                "sales", params=None, widgets=None, draft=False, compare=None, timeout_s=5
            )


@respx.mock
async def test_a_run_the_server_refuses_is_translated():
    respx.post(f"{BASE}/workspaces/ws/dashboards/gone/run").mock(
        return_value=httpx.Response(
            404, json={"error": "not_found", "detail": "No dashboard 'gone'."}
        )
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        with pytest.raises(GatewayError, match="Not found"):
            await _gateway(client=client, queue_wait_s=0).run_dashboard(
                "gone", params=None, widgets=None, draft=False, compare=None, timeout_s=5
            )


@respx.mock
async def test_a_run_that_outlasts_its_time_is_given_up():
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, text=_sse({"type": "done"}))

    respx.post(f"{BASE}/workspaces/ws/dashboards/sales/run").mock(side_effect=slow)
    async with httpx.AsyncClient(base_url=BASE) as client:
        with pytest.raises(GatewayError, match="did not finish"):
            await _gateway(client=client, queue_wait_s=0).run_dashboard(
                "sales", params=None, widgets=None, draft=False, compare=None, timeout_s=0.05
            )


@respx.mock
async def test_a_preview_is_reused_while_it_lasts_and_made_again_once_it_expires():
    held = respx.put(f"{BASE}/workspaces/ws/dashboard-previews/p-1").mock(
        side_effect=[httpx.Response(200, json={"id": "p-1"}), httpx.Response(404, json={})]
    )
    made = respx.post(f"{BASE}/workspaces/ws/dashboard-previews").mock(
        return_value=httpx.Response(201, json={"id": "p-2"})
    )
    spec = {"version": 1, "name": "assistant-preview"}
    async with httpx.AsyncClient(base_url=BASE) as client:
        gw = _gateway(client=client)
        assert await gw.hold_preview(spec, preview_id="p-1") == "p-1"
        assert not made.called
        assert await gw.hold_preview(spec, preview_id="p-1") == "p-2"
        assert await gw.hold_preview(spec, preview_id=None) == "p-2"

    assert held.call_count == 2 and made.call_count == 2
    assert json.loads(made.calls[0].request.content) == {"spec": spec}


@respx.mock
async def test_a_preview_the_server_refuses_says_why():
    respx.post(f"{BASE}/workspaces/ws/dashboard-previews").mock(
        return_value=httpx.Response(
            422,
            json={"error": "invalid_dashboard", "detail": "rows.0.widgets.0.query: unknown metric"},
        )
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        with pytest.raises(GatewayError, match="unknown metric"):
            await _gateway(client=client).hold_preview({}, preview_id=None)


@respx.mock
async def test_a_preview_runs_with_its_values():
    route = respx.post(f"{BASE}/workspaces/ws/dashboard-previews/p-1/run").mock(
        return_value=httpx.Response(200, text=_sse({"type": "widget", "id": "a"}))
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        events = await _gateway(client=client, queue_wait_s=0).run_preview(
            "p-1", params={"period": "last_7_days"}, timeout_s=5
        )
    assert events == [{"type": "widget", "id": "a"}]
    assert json.loads(route.calls[0].request.content) == {"params": {"period": "last_7_days"}}


@respx.mock
async def test_the_dashboard_schema_is_read_at_its_version():
    route = respx.get(f"{BASE}/dashboard-schema").mock(
        return_value=httpx.Response(200, json={"title": "Dashboard"})
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        assert await _gateway(client=client).dashboard_schema() == {"title": "Dashboard"}
    assert route.calls[0].request.url.params["version"] == "1"


@pytest.mark.parametrize(
    "record",
    [
        {"user_id": "someone-else", "on_behalf_of_id": "u-1"},
        # The assistant's own run, made for another person.
        {"user_id": "sa-1", "on_behalf_of_id": "u-2"},
    ],
)
@respx.mock
async def test_only_a_query_run_in_this_conversation_is_drawn(record):
    respx.get(f"{BASE}/queries/q1").mock(
        return_value=httpx.Response(200, json={"id": "q1", "status": "done", **record})
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        with pytest.raises(GatewayError) as exc:
            await _gateway(client=client, on_behalf_of_id="u-1").own_result("q1", limit=1000)
    assert exc.value.kind == "denied"


@respx.mock
async def test_a_query_still_running_has_nothing_to_draw():
    respx.get(f"{BASE}/queries/q1").mock(
        return_value=httpx.Response(
            200, json={"id": "q1", "status": "running", "user_id": "sa-1", "on_behalf_of_id": "u-1"}
        )
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        with pytest.raises(GatewayError, match="running"):
            await _gateway(client=client, on_behalf_of_id="u-1").own_result("q1", limit=1000)


@respx.mock
async def test_a_query_to_draw_comes_with_its_rows_uncut():
    respx.get(f"{BASE}/queries/q1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "q1",
                "status": "done",
                "user_id": "sa-1",
                "on_behalf_of_id": "u-1",
                "sql": "SELECT 1",
                "row_count": 300,
            },
        )
    )
    rows = respx.get(f"{BASE}/queries/q1/rows").mock(
        return_value=httpx.Response(
            200,
            json={"rows": [{"v": "x" * 50}] * 300, "columns": ["v"], "cursor": None, "total": 300},
        )
    )
    async with httpx.AsyncClient(base_url=BASE) as client:
        # A byte cap far below the rows: drawing them is not reading them.
        out = await _gateway(client=client, on_behalf_of_id="u-1").own_result("q1", limit=1000)

    assert rows.calls[0].request.url.params["limit"] == "1000"
    assert len(out["rows"]) == 300 and out["total"] == 300
    assert out["sql"] == "SELECT 1" and out["row_count"] == 300 and out["columns"] == ["v"]
