"""What the MCP endpoint advertises, and one call driven all the way through.

The end-to-end test is the point of this file: a real personal access token, a
real JSON-RPC request over the transport, and the real in-process loopback into
``api_app``. Everything between the wire and the database is the shipping code, so
it fails if the contract drifts rather than if a stub drifts.
"""

import pytest
from httpx import AsyncClient

from api.config import settings
from api.services.mcp.server import MCP_PATH, build_server

from ...conftest import seed_workspace
from .conftest import call_tool, rpc_body, rpc_headers

DATA_TOOLS = {
    "list_workspaces",
    "list_catalogs",
    "list_schemas",
    "list_tables",
    "describe_table",
    "run_sql",
    "get_query_result",
    "search_semantic",
    "get_semantic_model",
    "query_metric",
    "explain_metric",
}

DOCS_TOOLS = {"search_docs", "read_doc_page"}

#: Tools that read the caller's data and so must name a workspace. The
#: documentation tools are excluded on purpose: `docs/` is the same public
#: content for everyone, with no workspace to scope it to.
WORKSPACE_SCOPED = DATA_TOOLS - {"list_workspaces"}


async def _tools() -> dict:
    return {t.name: t for t in await build_server().list_tools()}


# --- the advertised surface ---------------------------------------------------


async def test_the_advertised_tools_are_exactly_the_intended_set():
    # The conftest points assistant_docs_dir at the repository's own docs/, so
    # the corpus is present and the documentation tools are registered.
    assert set(await _tools()) == DATA_TOOLS | DOCS_TOOLS


async def test_no_web_app_only_tool_leaks_in():
    """The assistant's worksheet tools have no meaning over MCP.

    `propose_sql_edit` and friends address a code editor that is not on the other
    end of this connection, so advertising them would be offering an agent a tool
    that can only ever no-op.
    """
    names = set(await _tools())
    assert not names & {"get_worksheet_sql", "get_worksheet_selection", "propose_sql_edit"}


async def test_every_data_tool_asks_for_a_workspace():
    tools = await _tools()
    for name in WORKSPACE_SCOPED:
        assert "workspace" in tools[name].input_schema["required"], name


async def test_the_documentation_tools_are_not_workspace_scoped():
    """`docs/` is the same public content for every caller.

    Asking for a workspace would imply the pages differ by one, and would make
    a product question fail for someone with no workspace membership at all.
    """
    tools = await _tools()
    for name in DOCS_TOOLS:
        assert "workspace" not in (tools[name].input_schema.get("properties") or {}), name


async def test_read_only_tools_say_so():
    for name, tool in (await _tools()).items():
        if name == "run_sql":
            continue
        assert tool.annotations.read_only_hint is True, name


async def test_the_documentation_tools_are_withheld_without_a_corpus(monkeypatch, tmp_path):
    """A tool in the schema is a tool the agent will call.

    Left registered on a deployment whose docs are missing, they would answer
    "not available" once per turn and teach the agent nothing it can act on —
    so they are withheld, exactly as the assistant withholds its own.
    """
    monkeypatch.setattr(settings, "assistant_docs_dir", tmp_path / "absent")
    assert set(await _tools()) == DATA_TOOLS


async def test_the_documentation_tools_follow_the_assistant_switch(monkeypatch):
    """One deployment-wide decision about whether AI may read the shipped docs.

    Two switches meaning almost the same thing is a setting an operator cannot
    reason about, so `ASSISTANT_DOCS_ENABLED=false` withholds them here too.
    """
    monkeypatch.setattr(settings, "assistant_docs_enabled", False)
    assert set(await _tools()) == DATA_TOOLS


async def test_the_instructions_only_mention_docs_tools_that_exist(monkeypatch):
    """Telling an agent to call a tool it has not been given wastes a turn."""
    assert "search_docs" in (build_server().instructions or "")

    monkeypatch.setattr(settings, "assistant_docs_enabled", False)
    assert "search_docs" not in (build_server().instructions or "")


async def test_run_sql_advertises_the_deployments_write_policy(monkeypatch):
    """The hint is how a client decides whether to warn before calling.

    It is a boot-time snapshot — the refusal itself is re-read per call — so it has
    to track the setting rather than being hard-coded either way.
    """
    tools = await _tools()
    assert tools["run_sql"].annotations.read_only_hint is True
    assert tools["run_sql"].annotations.destructive_hint is False

    monkeypatch.setattr(settings, "mcp_allow_writes", True)
    writable = {t.name: t for t in await build_server().list_tools()}
    assert writable["run_sql"].annotations.read_only_hint is False
    assert writable["run_sql"].annotations.destructive_hint is True


async def test_the_server_tells_agents_how_to_read_iceberg_columns():
    """Instructions carry the dialect rules an agent otherwise gets wrong.

    `information_schema.columns` does not report an Iceberg table's columns, and an
    agent that reaches for it gets a placeholder rather than an error — a silent
    wrong answer is exactly what the instructions exist to prevent.
    """
    instructions = build_server().instructions or ""
    assert "DESCRIBE" in instructions or "describe_table" in instructions
    assert "information_schema.columns" in instructions


# --- end to end ---------------------------------------------------------------


async def test_a_tool_call_runs_as_the_token_holder(
    mcp_client: AsyncClient, db_session, member, auth
):
    """One call, over the wire, through the loopback, to the database and back.

    `list_workspaces` is the honest smoke test: it returns exactly the workspaces
    this principal is a member of, so a result proves the caller's own identity
    reached `GET /workspaces` — not a service account's, and not nobody's.
    """
    await seed_workspace(db_session, user_id=member.id, slug="sales", name="Sales")

    headers, body = call_tool("list_workspaces")
    resp = await mcp_client.post(MCP_PATH, headers={**headers, **auth}, json=body)

    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == [
        {"workspace": "sales", "name": "Sales", "description": None, "default_catalog": "sales"}
    ]


async def test_a_workspace_the_caller_is_not_in_stays_invisible(
    mcp_client: AsyncClient, db_session, member, auth
):
    """Membership is the filter, and it is applied by the REST API, not by us.

    The MCP server adds no visibility rule of its own; this asserts it also
    subtracts none — a workspace someone else owns must not appear just because the
    call arrived over MCP.
    """
    from api.models.user import User
    from api.services.auth import hash_password

    other = User(email="other@test.local", password_hash=hash_password("x"), name="Other")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    await seed_workspace(db_session, user_id=other.id, slug="private", name="Private")
    await seed_workspace(db_session, user_id=member.id, slug="mine", name="Mine")

    headers, body = call_tool("list_workspaces")
    resp = await mcp_client.post(MCP_PATH, headers={**headers, **auth}, json=body)

    slugs = [w["workspace"] for w in resp.json()["result"]["structuredContent"]["result"]]
    assert slugs == ["mine"]


async def test_a_governed_denial_reaches_the_agent_as_a_readable_error(
    mcp_client: AsyncClient, db_session, member, auth
):
    """A 403 from the REST API has to arrive as something an agent can act on."""
    from api.models.user import User
    from api.services.auth import hash_password

    other = User(email="stranger@test.local", password_hash=hash_password("x"), name="Stranger")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    await seed_workspace(db_session, user_id=other.id, slug="locked", name="Locked")

    headers, body = call_tool("list_catalogs", {"workspace": "locked"})
    resp = await mcp_client.post(MCP_PATH, headers={**headers, **auth}, json=body)

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    rendered = payload.get("error", {}).get("message", "") + str(payload.get("result", ""))
    assert "list_catalogs" in rendered
    assert "Not found" in rendered or "Access denied" in rendered


async def test_a_write_is_refused_over_the_wire(mcp_client: AsyncClient, db_session, member, auth):
    """The refusal has to survive the trip, message intact, so an agent stops.

    An opaque failure here would read as a transient error and invite a retry loop
    against a policy that is never going to change mid-conversation.
    """
    await seed_workspace(db_session, user_id=member.id, slug="sales", name="Sales")

    headers, body = call_tool("run_sql", {"workspace": "sales", "sql": "DELETE FROM public.orders"})
    resp = await mcp_client.post(MCP_PATH, headers={**headers, **auth}, json=body)

    assert resp.status_code == 200, resp.text
    assert "read-only" in resp.text
    assert "MCP_ALLOW_WRITES" in resp.text


async def test_a_documentation_page_comes_back_over_the_wire(mcp_client: AsyncClient, auth):
    """The docs tools take no workspace, but still need a valid token.

    They read public content, so there is no grant to check — but they sit
    behind the same front door, and an anonymous caller must not reach them.
    """
    headers, body = call_tool("read_doc_page", {"path": "concepts/mcp-server.md"})
    resp = await mcp_client.post(MCP_PATH, headers={**headers, **auth}, json=body)

    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["isError"] is False
    # An unparameterised `dict` return carries no structured content, so the
    # text block is what the agent actually reads.
    assert "Model Context Protocol" in result["content"][0]["text"]

    anonymous = await mcp_client.post(MCP_PATH, headers=headers, json=body)
    assert anonymous.status_code == 401


async def test_an_unknown_tool_is_reported_as_unknown(mcp_client: AsyncClient, auth):
    headers, body = call_tool("drop_everything")
    resp = await mcp_client.post(MCP_PATH, headers={**headers, **auth}, json=body)
    assert "drop_everything" in resp.text


@pytest.mark.parametrize("method", ["tools/list", "tools/call"])
def test_the_transport_metadata_headers_are_part_of_the_contract(method):
    """The revision requires these mirrored headers on every POST.

    Asserted on the test helper because the docs' curl example and every client
    config depend on the same set; if the helper drifts, the docs are wrong too.
    """
    headers = rpc_headers(method, "x" if method == "tools/call" else None)
    assert headers["MCP-Protocol-Version"] == "2026-07-28"
    assert headers["Mcp-Method"] == method
    assert rpc_body(method)["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"]
