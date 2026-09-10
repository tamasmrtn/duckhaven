"""The tools themselves, called directly against a recording gateway.

No agent, no model, no HTTP: what matters here is that each tool reaches the
governed loopback with the arguments it was given, and that a governed refusal
arrives at the caller as something readable rather than "Error executing tool".
"""

from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from api.config import settings
from api.services.assistant.gateway import GatewayError
from api.services.mcp import tools
from api.services.mcp.auth import SCOPE_KEY, McpCall


class RecordingGateway:
    """Records what a tool asked for and returns a canned governed response."""

    def __init__(self, workspace: str, /, **overrides) -> None:
        self.workspace = workspace
        self.calls: list[tuple[str, tuple, dict]] = []
        self.overrides = overrides

    def _answer(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        outcome = self.overrides.get(name, [])
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


# --- documentation tools ------------------------------------------------------


async def test_read_doc_page_returns_a_shipped_page():
    page = await tools.read_doc_page("concepts/mcp-server.md")
    assert page["path"] == "concepts/mcp-server.md"
    assert "Model Context Protocol" in page["text"]
    assert page["version"] == settings.app_version


async def test_read_doc_page_suggests_near_misses():
    """An unknown path is a guess, not a fault, so it comes back correctable.

    Failing bare would leave the agent to guess again from the same information.
    """
    with pytest.raises(ToolError) as exc:
        await tools.read_doc_page("concepts/mcp.md")
    assert "Closest indexed paths" in str(exc.value)
    assert "concepts/mcp-server.md" in str(exc.value)


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
    assert (await tools.read_doc_page("concepts/mcp-server.md"))["title"]


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
        return [{"path": "concepts/mcp-server.md", "title": "MCP server"}]

    monkeypatch.setattr(tools, "search_pages", fake_search)
    monkeypatch.setattr(tools, "async_session_factory", _NullSessionFactory())

    out = await tools.search_docs("mcp")
    assert out["version"] == settings.app_version
    assert out["results"][0]["path"] == "concepts/mcp-server.md"


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

    `/queries/{id}/rows` authorizes on workspace membership alone, so the gateway
    compares the query's owner against the principal it was built for. Passing the
    wrong id here would let a reader page a workspace owner's results and exceed
    their own grants — so the id has to come from the authenticated call, not from
    anything the client sent.
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
