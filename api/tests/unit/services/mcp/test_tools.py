"""The tools themselves, called directly against a recording gateway.

No agent, no model, no HTTP: what matters here is that each tool reaches the
governed loopback with the arguments it was given, and that a governed refusal
arrives at the caller as something readable rather than "Error executing tool".
"""

from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from api.config import settings
from api.services.assistant.gateway import GatewayError
from api.services.mcp import dashboards, tools
from api.services.mcp.auth import SCOPE_KEY, McpCall


class RecordingGateway:
    """Records what a tool asked for and returns a canned governed response."""

    def __init__(self, workspace: str, /, **overrides) -> None:
        self.workspace = workspace
        self.calls: list[tuple[str, tuple, dict]] = []
        self.overrides = overrides

    def _answer(self, method: str, /, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        outcome = self.overrides.get(method, [])
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def __getattr__(self, name: str):
        async def call(*args, **kwargs):
            return self._answer(name, *args, **kwargs)

        return call


class Gateways(list):
    """The gateways the tools built, plus the canned answers they return."""

    def __init__(self) -> None:
        super().__init__()
        self.overrides: dict = {}


@pytest.fixture
def gateways(monkeypatch) -> Gateways:
    """Replace the tools' gateway factory; collect every gateway they build."""
    built = Gateways()

    def fake(ctx, workspace):
        gateway = RecordingGateway(workspace, **built.overrides)
        built.append(gateway)
        return gateway

    monkeypatch.setattr(tools, "_gateway", fake)
    return built


def ctx() -> SimpleNamespace:
    """A stand-in for the SDK Context; the tools only read the request off it."""
    return SimpleNamespace(request_context=SimpleNamespace(request=None))


# --- the write gate -----------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO s.t VALUES (1)",
        "DELETE FROM s.t",
        "UPDATE s.t SET a = 1",
        "CREATE TABLE s.t (a INT)",
        "DROP TABLE s.t",
    ],
)
async def test_writes_are_refused_by_default(gateways, sql):
    """No approval path exists over MCP, so a write must not run unattended."""
    with pytest.raises(ToolError) as exc:
        await tools.run_sql(ctx(), "ws", sql)
    assert "read-only" in str(exc.value)
    assert "MCP_ALLOW_WRITES" in str(exc.value)
    assert gateways == [], "the statement must not reach the gateway at all"


@pytest.mark.parametrize(
    "sql",
    [
        "SELCT * FROM orders",  # a typo, not a write
        "EXPLAIN SELECT 1",  # refused by the guard, but not for writing
        "ATTACH 'x.db'",  # likewise
    ],
)
async def test_sql_that_is_neither_a_read_nor_a_write_goes_to_the_server(gateways, sql):
    """The local gate answers one question: is this a write?

    Anything else — a typo, a statement DuckHaven refuses outright — is refused
    server-side for a reason that has nothing to do with write permission. Saying
    "ask your operator to enable writes" would send the agent after a setting that
    cannot help, on a turn it is told to treat as final. The server names the real
    reason instead, so these must reach it.
    """
    await tools.run_sql(ctx(), "ws", sql)
    assert gateways[0].calls[0][0] == "run_sql"


async def test_a_select_runs_while_read_only(gateways):
    await tools.run_sql(ctx(), "ws", "SELECT 1")
    assert gateways[0].calls[0][0] == "run_sql"


async def test_writes_run_once_an_operator_enables_them(gateways, monkeypatch):
    monkeypatch.setattr(settings, "mcp_allow_writes", True)
    await tools.run_sql(ctx(), "ws", "INSERT INTO s.t VALUES (1)")
    name, args, kwargs = gateways[0].calls[0]
    assert name == "run_sql"
    assert args[0] == "INSERT INTO s.t VALUES (1)"


async def test_the_write_gate_is_re_read_on_every_call(gateways, monkeypatch):
    """The tool annotation is fixed at boot; the refusal is not.

    An operator who flips the setting gets the new behaviour on the next call
    rather than on the next restart, so the two must not share a snapshot.
    """
    with pytest.raises(ToolError):
        await tools.run_sql(ctx(), "ws", "DELETE FROM s.t")
    monkeypatch.setattr(settings, "mcp_allow_writes", True)
    await tools.run_sql(ctx(), "ws", "DELETE FROM s.t")
    assert len(gateways) == 1


# --- workspace threading ------------------------------------------------------


async def test_every_workspace_scoped_tool_uses_the_workspace_it_was_given(gateways):
    """An MCP client has no ambient workspace, so the argument is the only source.

    A tool that quietly ignored it would read from whichever workspace happened to
    be first — a cross-workspace read with no error to notice it by.
    """
    calls = [
        tools.list_catalogs(ctx(), "sales"),
        tools.list_schemas(ctx(), "sales", "c"),
        tools.list_tables(ctx(), "sales", "c", "s"),
        tools.describe_table(ctx(), "sales", "c", "s", "t"),
        tools.get_table_lineage(ctx(), "sales", "c", "s", "t"),
        tools.run_sql(ctx(), "sales", "SELECT 1"),
        tools.get_query_result(ctx(), "sales", "q-1"),
        tools.search_semantic(ctx(), "sales", "revenue"),
        tools.get_semantic_model(ctx(), "sales", "m"),
        tools.explain_metric(ctx(), "sales", "m", "revenue"),
    ]
    for call in calls:
        await call
    assert [g.workspace for g in gateways] == ["sales"] * len(calls)


async def test_list_workspaces_is_not_workspace_scoped(gateways):
    await tools.list_workspaces(ctx())
    assert gateways[0].calls[0][0] == "list_workspaces"


# --- governed refusals reach the caller ---------------------------------------


async def test_a_governed_refusal_keeps_its_message(gateways):
    """The SDK replaces an arbitrary exception's text with a generic one.

    Only a ToolError's message survives to the client, and these messages are the
    ones worth surfacing: "Access denied: not authorized (reader) on c.s.t" tells
    an agent what to do next, where "Error executing tool" starts a guessing game.
    """
    gateways.overrides["list_catalogs"] = GatewayError(
        "Access denied: Not authorized (reader) on warehouse.public.orders"
    )
    with pytest.raises(ToolError) as exc:
        await tools.list_catalogs(ctx(), "ws")
    assert "Not authorized (reader) on warehouse.public.orders" in str(exc.value)


async def test_a_sql_guard_rejection_keeps_its_message(gateways):
    gateways.overrides["run_sql"] = GatewayError("Not allowed: ATTACH is denied")
    with pytest.raises(ToolError) as exc:
        await tools.run_sql(ctx(), "ws", "SELECT 1")
    assert "ATTACH is denied" in str(exc.value)


# --- lineage ------------------------------------------------------------------


async def test_get_table_lineage_forwards_the_walk_it_was_asked_for(gateways):
    await tools.get_table_lineage(
        ctx(),
        "sales",
        "warehouse",
        "public",
        "orders",
        direction="downstream",
        depth=3,
        columns_for=["k1"],
    )
    name, args, kwargs = gateways[0].calls[0]
    assert name == "table_lineage"
    assert args == ("warehouse", "public", "orders")
    assert kwargs == {"direction": "downstream", "depth": 3, "columns_for": ["k1"]}


async def test_get_table_lineage_defaults_to_a_shallow_walk_both_ways(gateways):
    """Depth is the only thing standing between an agent and a graph it cannot
    read, so the default has to be small rather than whatever the server allows."""
    await tools.get_table_lineage(ctx(), "sales", "warehouse", "public", "orders")
    _, _, kwargs = gateways[0].calls[0]
    assert kwargs == {"direction": "both", "depth": 2, "columns_for": None}


async def test_get_table_lineage_surfaces_a_governed_denial(gateways):
    """Lineage is gated on a metadata-tier grant for the leaf table."""
    gateways.overrides["table_lineage"] = GatewayError(
        "Access denied: Not authorized (metadata) on warehouse.public.orders"
    )
    with pytest.raises(ToolError) as exc:
        await tools.get_table_lineage(ctx(), "sales", "warehouse", "public", "orders")
    assert "Not authorized (metadata) on warehouse.public.orders" in str(exc.value)


# --- documentation tools ------------------------------------------------------


async def test_read_doc_page_returns_a_shipped_page():
    page = await tools.read_doc_page("ai/mcp-server.md")
    assert page["path"] == "ai/mcp-server.md"
    assert "Model Context Protocol" in page["text"]
    assert page["version"] == settings.app_version


async def test_read_doc_page_suggests_near_misses():
    """An unknown path is a guess, not a fault, so it comes back correctable.

    Failing bare would leave the agent to guess again from the same information.
    """
    with pytest.raises(ToolError) as exc:
        await tools.read_doc_page("concepts/mcp.md")
    assert "Closest indexed paths" in str(exc.value)
    assert "ai/mcp-server.md" in str(exc.value)


async def test_read_doc_page_cannot_escape_the_docs_tree():
    """The index is an allowlist, which is the security boundary as much as the
    usability one — no traversal reaches a file outside `docs/`."""
    for path in ("../api/src/api/config.py", "/etc/passwd", "concepts/../../README.md"):
        with pytest.raises(ToolError):
            await tools.read_doc_page(path)


async def test_read_doc_page_needs_no_authenticated_call():
    """It reads files off disk, so it touches neither the gateway nor a session.

    Called here with no request context at all: anything that reached for the
    caller's loopback client would raise instead of returning a page.
    """
    assert (await tools.read_doc_page("ai/mcp-server.md"))["title"]


async def test_search_docs_clamps_its_limit(monkeypatch):
    """The agent picks the limit, so the tool owns the bound rather than trusting it."""
    seen: list[int] = []

    async def fake_search(db, query, *, limit):
        seen.append(limit)
        return []

    monkeypatch.setattr(tools, "search_pages", fake_search)
    monkeypatch.setattr(tools, "async_session_factory", _NullSessionFactory())

    for asked, expected in ((0, 1), (-5, 1), (5, 5), (50, 10)):
        await tools.search_docs("time travel", limit=asked)
    assert seen == [1, 1, 5, 10]


async def test_search_docs_reports_a_failure_the_agent_can_read(monkeypatch):
    """A ToolError keeps its message; anything else is replaced with a generic one."""

    async def boom(db, query, *, limit):
        raise RuntimeError("relation docs_pages does not exist")

    monkeypatch.setattr(tools, "search_pages", boom)
    monkeypatch.setattr(tools, "async_session_factory", _NullSessionFactory())

    with pytest.raises(ToolError) as exc:
        await tools.search_docs("anything")
    assert "docs_pages does not exist" in str(exc.value)


async def test_search_docs_returns_the_running_version(monkeypatch):
    """The corpus ships in the image, so results describe *this* build.

    An agent that reports what a newer docs site says would describe features
    the deployment in front of it does not have.
    """

    async def fake_search(db, query, *, limit):
        return [{"path": "ai/mcp-server.md", "title": "MCP server"}]

    monkeypatch.setattr(tools, "search_pages", fake_search)
    monkeypatch.setattr(tools, "async_session_factory", _NullSessionFactory())

    out = await tools.search_docs("mcp")
    assert out["version"] == settings.app_version
    assert out["results"][0]["path"] == "ai/mcp-server.md"


class _NullSessionFactory:
    """Stands in for the session factory; the search itself is stubbed out."""

    def __call__(self):
        return self

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


# --- the principal a gateway is built for --------------------------------------


async def test_the_gateway_is_built_for_the_calling_principal():
    """Paging is restricted to the caller's own queries by this id.

    The gateway compares the query's owner against the principal it was built
    for, so the id has to come from the authenticated call, not from anything the
    client sent.
    """
    call = McpCall(user_id="user-42", client=object())
    request = SimpleNamespace(scope={SCOPE_KEY: call})
    gateway = tools._gateway(
        SimpleNamespace(request_context=SimpleNamespace(request=request)), "ws"
    )
    assert gateway._service_account_id == "user-42"
    assert gateway._ws == "ws"
    assert gateway._row_cap == settings.mcp_result_row_cap
    assert gateway._byte_cap == settings.mcp_result_byte_cap


async def test_the_gateway_tags_queries_with_the_calling_client():
    call = McpCall(user_id="user-42", client=object(), caller="claude-code/2.1.0")
    request = SimpleNamespace(scope={SCOPE_KEY: call})
    gateway = tools._gateway(
        SimpleNamespace(request_context=SimpleNamespace(request=request)), "ws"
    )
    assert gateway._tags == {"mcp.client": "claude-code/2.1.0"}


async def test_an_unnamed_client_adds_no_tag():
    call = McpCall(user_id="user-42", client=object())
    request = SimpleNamespace(scope={SCOPE_KEY: call})
    gateway = tools._gateway(
        SimpleNamespace(request_context=SimpleNamespace(request=request)), "ws"
    )
    assert gateway._tags == {}


async def test_query_metric_tags_its_query_with_the_model_and_metrics(gateways):
    gateways.overrides["compile_metric_query"] = {"sql": "SELECT 1"}
    gateways.overrides["run_sql"] = {"rows": []}

    await tools.query_metric(ctx(), "ws", "sales", ["revenue", "orders"])

    (_, _, kwargs) = next(c for c in gateways[0].calls if c[0] == "run_sql")
    assert kwargs["tags"] == {"semantic.model": "sales", "semantic.metrics": "revenue,orders"}


# --- dashboards ---------------------------------------------------------------

FILE = """\
version: 1
name: sales
title: Sales
rows:
  - widgets:
      - id: revenue
        type: kpi
        title: Revenue
        query: {model: orders, metrics: [revenue]}
      - id: notes
        type: text
        markdown: Net of refunds.
"""


def _kpi(widget_id: str) -> dict:
    return {
        "id": widget_id,
        "type": "kpi",
        "title": "Revenue",
        "query": {"model": "orders", "metrics": ["revenue"]},
    }


def _ran(*widget_ids: str) -> list[dict]:
    """The events of a run of these widgets, each a one-number kpi."""
    start = {"type": "start", "widgets": list(widget_ids), "parameters": []}
    return [start] + [
        {
            "type": "widget",
            "id": w,
            "state": "ok",
            "scope": {},
            "rows": {
                "columns": [{"name": "revenue"}],
                "data": [["10"]],
                "truncated": False,
                "query_id": f"q-{w}",
            },
            "chart": {"type": "kpi", "y": [{"field": "revenue"}]},
        }
        for w in widget_ids
    ]


async def test_the_schema_tool_hands_out_the_guide_first(gateways):
    assert await tools.get_dashboard_schema() == {"guide": dashboards.GUIDE}
    schema = (await tools.get_dashboard_schema("schema"))["schema"]
    assert schema["title"] == "DuckHaven dashboard"
    # The file format is the same for everyone: there is nothing to govern.
    assert gateways == []


async def test_the_dashboard_tools_use_the_workspace_they_were_given(gateways):
    gateways.overrides["validate_dashboard"] = {"valid": True, "errors": [], "warnings": []}
    await tools.list_dashboards(ctx(), "sales", query="rev", uses="orders.revenue")
    await tools.get_dashboard(ctx(), "sales", "overview", draft=True)
    await tools.validate_dashboard(ctx(), "sales", FILE)
    await tools.run_dashboard(
        ctx(), "sales", "overview", widgets=["revenue"], params={"period": "last_7_days"}
    )

    assert [g.workspace for g in gateways] == ["sales"] * 4
    assert [g.calls[0] for g in gateways] == [
        ("list_dashboards", (), {"query": "rev", "uses": "orders.revenue"}),
        ("get_dashboard", ("overview",), {"draft": True}),
        ("validate_dashboard", (FILE,), {}),
        (
            "run_dashboard",
            ("overview",),
            {
                "params": {"period": "last_7_days"},
                "widgets": ["revenue"],
                "draft": False,
                "compare": None,
                "timeout_s": settings.mcp_query_timeout_s,
            },
        ),
    ]


async def test_validation_returns_its_problems_up_to_a_cap(gateways):
    problem = {"path": "rows[0]", "line": 5, "col": 3, "message": "Bad.", "hint": None}
    gateways.overrides["validate_dashboard"] = {
        "valid": False,
        "errors": [problem] * 30,
        "warnings": [{**problem, "message": "Unused."}],
        "refs": [{"kind": "model", "model": "orders", "name": None}],
    }

    out = await tools.validate_dashboard(ctx(), "sales", FILE)

    assert out["valid"] is False
    assert out["errors"][0] == {"path": "rows[0]", "line": 5, "col": 3, "message": "Bad."}
    assert len(out["errors"]) == 25 and out["more_errors"] == 5
    assert out["warnings"] == [{"path": "rows[0]", "line": 5, "col": 3, "message": "Unused."}]
    assert "refs" not in out


async def test_a_dashboard_tool_refused_by_the_server_says_why(gateways):
    gateways.overrides["validate_dashboard"] = GatewayError("Access denied: writer role required")
    with pytest.raises(ToolError, match="writer role required"):
        await tools.validate_dashboard(ctx(), "sales", FILE)


async def test_a_preview_holds_the_file_as_written_and_runs_its_data_widgets(gateways):
    gateways.overrides["hold_preview"] = "p-1"
    gateways.overrides["run_preview"] = _ran("revenue")

    out = await tools.preview_dashboard(ctx(), "sales", FILE, params={"period": "last_7_days"})

    hold, run = gateways[0].calls
    assert hold == ("hold_preview", (FILE,), {"preview_id": None})
    assert run == (
        "run_preview",
        ("p-1",),
        {
            "params": {"period": "last_7_days"},
            "widgets": ["revenue"],
            "timeout_s": settings.mcp_query_timeout_s,
        },
    )
    assert [w["widget"] for w in out["widgets"]] == ["revenue"]
    assert out["widgets"][0]["encoding"] == "kpi: value=revenue"
    assert out["widgets"][0]["lint"] == []


async def test_a_preview_takes_the_file_as_an_object_too(gateways):
    import yaml

    gateways.overrides["hold_preview"] = "p-1"
    gateways.overrides["run_preview"] = _ran("revenue")
    spec = yaml.safe_load(FILE)

    await tools.preview_dashboard(ctx(), "sales", spec)

    assert gateways[0].calls[0] == ("hold_preview", (spec,), {"preview_id": None})


async def test_a_preview_runs_the_first_twelve_widgets_unless_told_which(gateways):
    gateways.overrides["hold_preview"] = "p-1"
    ids = [f"k{i}" for i in range(13)]
    spec = {
        "version": 1,
        "name": "many",
        "title": "Many",
        "rows": [
            {"widgets": [_kpi(w) for w in ids[:8]]},
            {"widgets": [_kpi(w) for w in ids[8:]]},
        ],
    }

    await tools.preview_dashboard(ctx(), "sales", spec)
    assert gateways[0].calls[1][2]["widgets"] == ids[:12]

    await tools.preview_dashboard(ctx(), "sales", spec, widgets=["k12"])
    assert gateways[1].calls[1][2]["widgets"] == ["k12"]

    with pytest.raises(ToolError, match="between 1 and 12"):
        await tools.preview_dashboard(ctx(), "sales", spec, widgets=ids)


@pytest.mark.parametrize("widget", ["nope", "notes"])
async def test_a_preview_of_a_widget_the_file_does_not_run_names_the_ones_it_does(gateways, widget):
    with pytest.raises(ToolError, match="Its widgets are: revenue"):
        await tools.preview_dashboard(ctx(), "sales", FILE, widgets=[widget])
    assert gateways == []


async def test_a_file_with_only_text_has_nothing_to_preview(gateways):
    text_only = {
        "version": 1,
        "name": "notes",
        "title": "Notes",
        "rows": [{"widgets": [{"id": "notes", "type": "text", "markdown": "Hello."}]}],
    }
    with pytest.raises(ToolError, match="every widget in this file is a text widget"):
        await tools.preview_dashboard(ctx(), "sales", text_only)


async def test_an_invalid_file_is_refused_with_every_problem_before_anything_runs(gateways):
    broken = FILE.replace("        title: Revenue\n", "        title: Revenue\n        widht: 4\n")

    with pytest.raises(ToolError) as exc:
        await tools.preview_dashboard(ctx(), "sales", broken)

    message = str(exc.value)
    assert message.startswith("Nothing ran: the file has 1 problem.")
    assert "rows[0].widgets[0].widht (line 9, col 9)" in message
    assert "Did you mean 'width'?" in message
    assert gateways == []


async def test_a_file_with_many_problems_lists_the_first_of_them(gateways):
    spec = {
        "version": 1,
        "name": "many",
        "title": "Many",
        "rows": [{"widgets": [{**_kpi(f"k{r}{i}"), "x": 1} for i in range(8)]} for r in range(4)],
    }
    with pytest.raises(ToolError) as exc:
        await tools.preview_dashboard(ctx(), "sales", spec)
    message = str(exc.value)
    assert message.startswith("Nothing ran: the file has 32 problems.")
    assert message.count("\n- rows") == 25
    assert message.endswith("- and 7 more.")


async def test_a_preview_the_server_refuses_says_why(gateways):
    gateways.overrides["hold_preview"] = GatewayError("Access denied: writer role required")
    with pytest.raises(ToolError, match="writer role required"):
        await tools.preview_dashboard(ctx(), "sales", FILE)


async def test_a_run_of_too_many_widgets_is_refused_before_it_starts(gateways):
    with pytest.raises(ToolError, match="between 1 and 12"):
        await tools.run_dashboard(ctx(), "sales", "overview", widgets=[f"w{i}" for i in range(13)])
    assert gateways == []


async def test_a_run_of_every_widget_shows_twelve_and_names_the_rest(gateways):
    ids = [f"w{i}" for i in range(14)]
    gateways.overrides["run_dashboard"] = _ran(*ids)

    out = await tools.run_dashboard(ctx(), "sales", "overview", rows=1)

    assert gateways[0].calls[0][2]["widgets"] is None
    assert len(out["widgets"]) == 12
    assert out["not_shown"] == ["w12", "w13"]
    assert "lint" not in out["widgets"][0]


async def test_a_run_the_server_refuses_says_why(gateways):
    gateways.overrides["run_dashboard"] = GatewayError("Not found (or not accessible): overview")
    with pytest.raises(ToolError, match="Not found"):
        await tools.run_dashboard(ctx(), "sales", "overview")


TREND = {"id": "trend", "type": "line", "title": "Revenue", "columns": {"x": "m", "y": ["r"]}}


def _own(rows: list[dict]) -> dict:
    return {
        "query_id": "q-7",
        "sql": "SELECT m, r FROM t",
        "row_count": len(rows),
        "columns": ["m", "r"],
        "column_schema": None,
        "rows": rows,
        "total": len(rows),
    }


async def test_show_chart_returns_the_widget_and_a_table_to_show(gateways):
    gateways.overrides["own_result"] = _own(
        [{"m": "2026-09-01", "r": 4}, {"m": "2026-10-01", "r": 6}]
    )

    out = await tools.show_chart(ctx(), "sales", "q-7", TREND)

    assert gateways[0].calls[0] == ("own_result", ("q-7",), {"limit": 1000})
    assert out["widget"] == {**TREND, "sql": "SELECT m, r FROM t"}
    assert out["encoding"] == "line: x=m, y=r"
    assert out["table"].splitlines() == [
        "| m | r |",
        "|---|---|",
        "| 2026-09-01 | 4 |",
        "| 2026-10-01 | 6 |",
    ]
    assert out["row_count"] == 2 and out["lint"] == []


async def test_show_chart_refuses_a_widget_that_brings_its_own_query(gateways):
    gateways.overrides["own_result"] = _own([{"m": "2026-09-01", "r": 4}])
    with pytest.raises(ToolError, match="not a query or SQL"):
        await tools.show_chart(ctx(), "sales", "q-7", {**TREND, "sql": "SELECT 1"})


async def test_show_chart_refuses_a_query_someone_else_ran(gateways):
    gateways.overrides["own_result"] = GatewayError("I can only draw results of queries I ran.")
    with pytest.raises(ToolError, match="only draw results"):
        await tools.show_chart(ctx(), "sales", "q-x", TREND)


async def test_the_dashboard_resource_reads_the_file_as_the_caller(gateways):
    gateways.overrides["dashboard_source"] = FILE
    assert await tools.read_dashboard_file(ctx(), "sales", "overview") == FILE
    assert gateways[0].workspace == "sales"
    assert gateways[0].calls == [("dashboard_source", ("overview",), {})]

    gateways.overrides["dashboard_source"] = GatewayError("Not found (or not accessible): x")
    with pytest.raises(ResourceError, match="Not found"):
        await tools.read_dashboard_file(ctx(), "sales", "x")


# --- dashboard drafts ---------------------------------------------------------


def _board(name: str = "sales", version: int = 4, status: str = "draft") -> dict:
    """A dashboard as the API returns it with ``?draft=true``."""
    import yaml

    return {
        "name": name,
        "title": "Sales",
        "version": version,
        "status": status,
        "spec": yaml.safe_load(FILE),
    }


async def test_a_draft_of_a_new_dashboard_is_created(gateways):
    gateways.overrides["dashboard_draft"] = None
    gateways.overrides["save_dashboard_draft"] = _board(version=1)

    out = await tools.save_dashboard_draft(ctx(), "acme", FILE)

    assert gateways[0].workspace == "acme"
    assert gateways[0].calls == [
        ("dashboard_draft", ("sales",), {}),
        ("save_dashboard_draft", (FILE,), {"name": None, "base_version": None}),
    ]
    assert out["saved"] is True and out["published"] is False
    assert (out["dashboard"], out["version"]) == ("sales", 1)
    assert out["path"] == "/acme/dashboards/sales/edit"
    assert "an owner of the workspace reviews the draft and publishes it" in out["note"]


async def test_a_draft_does_not_replace_one_it_has_not_read(gateways):
    gateways.overrides["dashboard_draft"] = _board()

    with pytest.raises(ToolError) as exc:
        await tools.save_dashboard_draft(ctx(), "acme", FILE)

    assert "exists, at version 4" in str(exc.value)
    assert "get_dashboard(draft=true)" in str(exc.value)
    assert [c[0] for c in gateways[0].calls] == ["dashboard_draft"]


async def test_a_draft_replaces_the_version_it_was_read_at(gateways):
    gateways.overrides["dashboard_draft"] = _board(name="sales-v2", status="published")
    gateways.overrides["save_dashboard_draft"] = _board(name="sales-v2", version=5)

    out = await tools.save_dashboard_draft(ctx(), "acme", FILE, base_version=4)

    # The live name, should the file use one the dashboard had before.
    assert gateways[0].calls[1] == (
        "save_dashboard_draft",
        (FILE,),
        {"name": "sales-v2", "base_version": 4},
    )
    assert out["version"] == 5


async def test_an_invalid_draft_is_refused_before_anything_is_sent(gateways):
    broken = FILE.replace("title: Sales\n", "title: Sales\nwidht: 4\n")

    with pytest.raises(ToolError) as exc:
        await tools.save_dashboard_draft(ctx(), "acme", broken)

    assert str(exc.value).startswith("Nothing was saved: the file has 1 problem.")
    assert gateways == []


@pytest.mark.parametrize(
    ("call", "refusal"),
    [
        ("save_dashboard_draft", "Limit reached: Try again in 12 minutes."),
        ("save_dashboard_draft", "Conflict: The dashboard changed since version 4."),
        ("dashboard_draft", "Access denied: writer role required"),
    ],
)
async def test_a_draft_the_server_refuses_says_why(gateways, call, refusal):
    gateways.overrides["dashboard_draft"] = _board()
    gateways.overrides[call] = GatewayError(refusal)

    with pytest.raises(ToolError, match=refusal):
        await tools.save_dashboard_draft(ctx(), "acme", FILE, base_version=4)


async def test_drafts_are_refused_once_an_operator_turns_them_off(gateways, monkeypatch):
    monkeypatch.setattr(settings, "mcp_allow_dashboard_drafts", False)

    for call in (
        tools.save_dashboard_draft(ctx(), "acme", FILE),
        tools.add_widget_to_draft(ctx(), "acme", "sales", _kpi("orders")),
    ):
        with pytest.raises(ToolError, match="MCP_ALLOW_DASHBOARD_DRAFTS"):
            await call
    assert gateways == []


async def test_a_widget_is_added_to_the_draft_it_was_read_from(gateways):
    gateways.overrides["dashboard_draft"] = _board(version=4)
    gateways.overrides["save_dashboard_draft"] = _board(version=5)

    out = await tools.add_widget_to_draft(ctx(), "acme", "sales", _kpi("orders"), row=0)

    name, (data,), kwargs = gateways[0].calls[1]
    assert (name, kwargs) == ("save_dashboard_draft", {"name": "sales", "base_version": 4})
    assert [w["id"] for w in data["rows"][0]["widgets"]] == ["revenue", "notes", "orders"]
    assert out["version"] == 5


async def test_a_widget_without_a_row_gets_one_of_its_own(gateways):
    gateways.overrides["dashboard_draft"] = _board()
    gateways.overrides["save_dashboard_draft"] = _board(version=5)

    await tools.add_widget_to_draft(ctx(), "acme", "sales", _kpi("orders"))

    data = gateways[0].calls[1][1][0]
    assert [[w["id"] for w in r["widgets"]] for r in data["rows"]] == [
        ["revenue", "notes"],
        ["orders"],
    ]


@pytest.mark.parametrize(
    ("widget", "row", "refusal"),
    [
        ("revenue", None, "The dashboard already has a widget 'revenue'."),
        ("orders", 3, "There is no row 3; the dashboard has 1, from 0."),
    ],
)
async def test_a_widget_that_does_not_fit_is_refused_with_the_reason(
    gateways, widget, row, refusal
):
    gateways.overrides["dashboard_draft"] = _board()

    with pytest.raises(ToolError) as exc:
        await tools.add_widget_to_draft(ctx(), "acme", "sales", _kpi(widget), row=row)

    assert str(exc.value) == refusal
    assert [c[0] for c in gateways[0].calls] == ["dashboard_draft"]


async def test_a_widget_for_a_dashboard_that_is_not_there_names_where_to_look(gateways):
    gateways.overrides["dashboard_draft"] = None

    with pytest.raises(ToolError, match="no dashboard 'nope'. Find one with list_dashboards"):
        await tools.add_widget_to_draft(ctx(), "acme", "nope", _kpi("orders"))
