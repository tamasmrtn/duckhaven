"""The assistant's tools: thin wrappers over the governed loopback gateway.

Each tool is a plain async function; Pydantic AI derives its JSON schema from the
signature and docstring. Tools never touch the database, DuckDB, or Polaris — they
only call :class:`~api.services.assistant.gateway.Gateway`, which goes through the
governed REST API as the service account.
"""

from __future__ import annotations

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext

from api.services.assistant.deps import AssistantDeps
from api.services.assistant.gateway import GatewayError
from api.services.sql_guard import is_read_only


async def list_catalogs(ctx: RunContext[AssistantDeps]) -> list[dict]:
    """List the catalogs visible in this workspace (slug and display name)."""
    try:
        return await ctx.deps.gateway.list_catalogs()
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def list_schemas(ctx: RunContext[AssistantDeps], catalog: str) -> list[str]:
    """List the schemas in a catalog.

    Args:
        catalog: The catalog slug (from ``list_catalogs``).
    """
    try:
        return await ctx.deps.gateway.list_schemas(catalog)
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def list_tables(ctx: RunContext[AssistantDeps], catalog: str, schema: str) -> list[str]:
    """List the tables in a schema.

    Args:
        catalog: The catalog slug.
        schema: The schema name.
    """
    try:
        return await ctx.deps.gateway.list_tables(catalog, schema)
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def describe_table(
    ctx: RunContext[AssistantDeps], catalog: str, schema: str, table: str
) -> dict:
    """Describe a table: its columns (name, type, nullability), row count, and size.

    Args:
        catalog: The catalog slug.
        schema: The schema name.
        table: The table name.
    """
    try:
        return await ctx.deps.gateway.describe_table(catalog, schema, table)
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def run_sql(ctx: RunContext[AssistantDeps], sql: str) -> dict:
    """Run a SQL statement against the governed catalogs and return a result sample.

    Prefer a single ``SELECT``. The result is capped to a sample of rows for you to
    reason over; the full result is available to the user in the UI. Qualify tables
    as ``schema.table`` (or ``catalog.schema.table``). Non-SELECT statements (writes)
    are only permitted when this assistant is granted write access, and require the
    user to approve them.

    Args:
        sql: The SQL to execute.
    """
    if not is_read_only(sql):
        if not ctx.deps.can_write:
            raise ModelRetry("This assistant is read-only; only SELECT statements are permitted.")
        if not ctx.tool_call_approved:
            # Defer to the human: the run ends with a DeferredToolRequests output and
            # the UI shows an approve/deny prompt. On approval the tool re-runs with
            # tool_call_approved=True.
            raise ApprovalRequired()
    try:
        return await ctx.deps.gateway.run_sql(
            sql, catalog=ctx.deps.catalog, timeout_s=ctx.deps.query_timeout_s
        )
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def get_query_result(
    ctx: RunContext[AssistantDeps], query_id: str, cursor: str | None = None
) -> dict:
    """Fetch the next page of rows for a query you previously ran.

    Args:
        query_id: The ``query_id`` returned by ``run_sql``.
        cursor: The ``cursor`` from the previous page, or omit for the first page.
    """
    try:
        return await ctx.deps.gateway.get_query_result(
            query_id, cursor=cursor, limit=ctx.deps.gateway._row_cap
        )
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def get_worksheet_sql(ctx: RunContext[AssistantDeps]) -> str:
    """Return the SQL currently in the user's worksheet editor.

    Call this before proposing an edit so you build on what the user already has.
    Returns an empty-editor note when nothing is open.
    """
    return ctx.deps.editor_sql or "(the worksheet editor is empty or not open)"


async def propose_sql_edit(ctx: RunContext[AssistantDeps], sql: str, explanation: str) -> str:
    """Propose replacing the SQL in the user's worksheet editor.

    Use this when the user asks you to write, fix, or change the SQL in their
    editor. The proposed SQL is shown in their editor as a highlighted change that
    they accept or reject — it is not executed. Always provide the complete new SQL
    for the worksheet, not a fragment or a diff.

    Args:
        sql: The full proposed SQL for the worksheet.
        explanation: A one-line summary of what changed and why.
    """
    return "Proposed the edit in the user's editor; they will accept or reject it."


ALL_TOOLS = [
    list_catalogs,
    list_schemas,
    list_tables,
    describe_table,
    run_sql,
    get_query_result,
    get_worksheet_sql,
    propose_sql_edit,
]
