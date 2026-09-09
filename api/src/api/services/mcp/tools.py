"""The MCP tools: thin wrappers over the governed loopback gateway.

Each tool is a plain async function; the MCP SDK derives its JSON schema from the
signature and docstring. Every one reaches DuckHaven's data only through
:class:`~api.services.assistant.gateway.Gateway`, over the REST API as the calling
principal -- never the database, DuckDB or Polaris directly. That is what bounds
this server to the caller's own grants.

Two differences from the assistant's tool set, both forced by the shape of an MCP
connection rather than chosen:

* Every tool takes an explicit ``workspace``. A worksheet has an ambient workspace
  in its URL; an MCP client has nothing, so the agent discovers its reach with
  ``list_workspaces`` and names one on each call.
* A governed refusal is re-raised as :class:`ToolError`. The SDK carries a
  ``ToolError``'s message to the caller and replaces any other exception's text
  with a generic one, so this is the difference between an agent reading "Access
  denied: not authorized (reader) on c.s.t" and reading "Error executing tool".
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from api.config import settings
from api.services.assistant.gateway import Gateway, GatewayError
from api.services.mcp.auth import current_call
from api.services.sql_guard import is_read_only

Workspace = Annotated[str, Field(description="Workspace slug, from `list_workspaces`.")]


def _gateway(ctx: Context, workspace: str) -> Gateway:
    """A gateway scoped to one workspace, on this request's authenticated client."""
    call = current_call(ctx.request_context.request)
    return Gateway(
        call.client,
        workspace,
        row_cap=settings.mcp_result_row_cap,
        byte_cap=settings.mcp_result_byte_cap,
        # The principal queries are attributed to. Named for the assistant, which
        # is the only other caller; here it is the human or service account whose
        # token this request carried.
        service_account_id=call.user_id,
    )


# ── Discovery ────────────────────────────────────────────────────────────────
async def list_workspaces(ctx: Context) -> list[dict]:
    """List the DuckHaven workspaces your access token can reach.

    Call this first. Every other tool needs a workspace slug, and a workspace you
    are not a member of is invisible here rather than forbidden — if one you
    expected is missing, ask an administrator for membership.
    """
    try:
        # The only call here that is not workspace-scoped, so the gateway's slug
        # goes unused rather than being a workspace this caller must already know.
        return await _gateway(ctx, "").list_workspaces()
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


async def list_catalogs(ctx: Context, workspace: Workspace) -> list[dict]:
    """List the catalogs visible in a workspace (slug and display name)."""
    try:
        return await _gateway(ctx, workspace).list_catalogs()
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


async def list_schemas(ctx: Context, workspace: Workspace, catalog: str) -> list[str]:
    """List the schemas in a catalog.

    Args:
        catalog: The catalog slug (from `list_catalogs`).
    """
    try:
        return await _gateway(ctx, workspace).list_schemas(catalog)
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


async def list_tables(ctx: Context, workspace: Workspace, catalog: str, schema: str) -> list[str]:
    """List the tables in a schema.

    Args:
        catalog: The catalog slug.
        schema: The schema name.
    """
    try:
        return await _gateway(ctx, workspace).list_tables(catalog, schema)
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


async def describe_table(
    ctx: Context, workspace: Workspace, catalog: str, schema: str, table: str
) -> dict:
    """Describe a table: its columns (name, type, nullability), row count, and size.

    This is how you read an Iceberg table's columns. Do not query
    `information_schema.columns` — it does not report them.

    Args:
        catalog: The catalog slug.
        schema: The schema name.
        table: The table name.
    """
    try:
        return await _gateway(ctx, workspace).describe_table(catalog, schema, table)
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


# ── SQL ──────────────────────────────────────────────────────────────────────
async def run_sql(ctx: Context, workspace: Workspace, sql: str, catalog: str | None = None) -> dict:
    """Run SQL against the governed catalogs and return a sample of the result.

    Qualify tables as `catalog.schema.table` (or `schema.table` when you pass
    `catalog`). The dialect is DuckDB reading Apache Iceberg.

    The result is capped to a sample of rows; the returned `query_id` and `cursor`
    page the rest through `get_query_result`. A `semantic_warning` in the result
    means a published metric already defines what this query aggregates, and that
    definition may carry filters yours does not — prefer `query_metric`, or say
    explicitly that you are computing something different.

    Args:
        sql: The SQL to execute.
        catalog: Catalog to resolve unqualified names against. Optional.
    """
    if not is_read_only(sql) and not settings.mcp_allow_writes:
        raise ToolError(
            "This MCP server is read-only: only SELECT statements are permitted. "
            "Writes are disabled because there is no way to ask a human to approve "
            "one over MCP. An operator can enable them with MCP_ALLOW_WRITES=true."
        )
    try:
        return await _gateway(ctx, workspace).run_sql(
            sql, catalog=catalog, timeout_s=settings.mcp_query_timeout_s
        )
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


async def get_query_result(
    ctx: Context, workspace: Workspace, query_id: str, cursor: str | None = None
) -> dict:
    """Fetch the next page of rows for a query you ran.

    Only queries run by your own token can be paged.

    Args:
        query_id: The `query_id` returned by `run_sql` or `query_metric`.
        cursor: The `cursor` from the previous page, or omit for the first page.
    """
    try:
        return await _gateway(ctx, workspace).get_query_result(
            query_id, cursor=cursor, limit=settings.mcp_result_row_cap
        )
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


# ── Semantic layer ───────────────────────────────────────────────────────────
async def search_semantic(ctx: Context, workspace: Workspace, query: str) -> dict:
    """Find the curated metrics and dimensions a question is about.

    Call this FIRST for any question about a business measure — revenue, orders,
    customers, conversion, churn — before browsing the catalog. Curated
    definitions are authoritative: when one exists, use it rather than working the
    calculation out yourself from column names.

    When `ambiguous` is non-empty, more than one authoritative metric matches the
    words equally well and they mean different things — ask which was meant
    instead of picking one. When `broken` is non-empty, a matching definition
    exists but cannot currently be used; say so and say why, rather than reporting
    it as missing or computing a replacement.

    Args:
        query: The question, or the business term to look up.
    """
    try:
        return await _gateway(ctx, workspace).search_semantic(query)
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


async def get_semantic_model(ctx: Context, workspace: Workspace, model: str) -> dict:
    """Read one semantic model: its metrics, dimensions, datasets and joins.

    Use after `search_semantic` tells you which model is relevant, to see what else
    can be asked of it — which dimensions exist, which time grains a date supports,
    and what each metric actually computes.

    Args:
        model: The model slug, from `search_semantic`.
    """
    try:
        return await _gateway(ctx, workspace).get_semantic_model(model)
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


async def query_metric(
    ctx: Context,
    workspace: Workspace,
    model: str,
    metrics: list[str],
    dimensions: list[str] | None = None,
    grain: str | None = None,
    time_window: dict | None = None,
    filters: list[dict] | None = None,
    order_by: list[dict] | None = None,
    limit: int | None = None,
) -> dict:
    """Answer a question using curated metric definitions, and run it.

    Prefer this over `run_sql` whenever a curated metric covers the question. The
    SQL is generated from the stored definitions — the aggregation, its filters,
    the join path and the correct date column — so the answer matches what the
    organization has agreed these words mean. You do not write the aggregation;
    you choose the concepts.

    Args:
        model: Model slug from `search_semantic`.
        metrics: Metric names to compute. All must belong to the same dataset.
        dimensions: Dimension names to group by.
        grain: Time grain to group by — one of day, week, month, quarter, year.
        time_window: The period to restrict to. State it explicitly, because
            "last month" is ambiguous. One of:
            `{"kind": "last_complete", "grain": "month", "n": 1}` — the last N
            *complete* periods, excluding the one in progress (this is what "last
            month" usually means);
            `{"kind": "trailing", "grain": "day", "n": 30}` — a rolling window of
            N periods ending today, including today ("the last 30 days");
            `{"kind": "to_date", "grain": "year"}` — period start through today;
            `{"kind": "absolute", "start": "2026-01-01", "end": "2026-02-01"}` —
            explicit dates, end exclusive.
        filters: Predicates on dimensions, e.g.
            `[{"dimension": "country", "op": "in", "values": ["United States"]}]`.
            Operators: eq, ne, in, not_in, gt, gte, lt, lte, contains, is_null,
            is_not_null. Match values against the dimension's sample values.
        order_by: e.g. `[{"field": "revenue", "descending": true}]`. Fields must be
            metrics or dimensions in the result.
        limit: Maximum rows.
    """
    body: dict[str, Any] = {"model": model, "metrics": metrics}
    if dimensions:
        body["dimensions"] = dimensions
    if grain:
        body["grain"] = grain
    if time_window:
        body["time_range"] = time_window
    if filters:
        body["filters"] = filters
    if order_by:
        body["order_by"] = order_by
    if limit is not None:
        body["limit"] = limit

    gateway = _gateway(ctx, workspace)
    try:
        # The compiler refuses rather than approximating, and its messages name the
        # legal alternatives — so handing this straight back lets the agent correct
        # itself instead of falling back to inventing SQL.
        compiled = await gateway.compile_metric_query(body)
        result = await gateway.run_sql(
            compiled["sql"], catalog=None, timeout_s=settings.mcp_query_timeout_s
        )
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc

    # Compiled SQL never re-triggers the "you should have used a metric" nudge.
    result.pop("semantic_warning", None)
    result["sql"] = compiled["sql"]
    result["definitions_used"] = compiled.get("definitions_used", [])
    if compiled.get("warnings"):
        result["notes"] = compiled["warnings"]
    return result


async def explain_metric(ctx: Context, workspace: Workspace, model: str, metric: str) -> dict:
    """Explain what a curated metric means and how it is calculated.

    Use for "how is X calculated?", "what does X include?", "which metric should I
    use for X?". Answer from what this returns rather than inferring from column
    names — the stored definition is what the organization agreed, and a
    plausible-sounding guess is exactly what it exists to replace.

    Args:
        model: The model slug.
        metric: The metric name.
    """
    try:
        return await _gateway(ctx, workspace).metric_definition(model, metric)
    except GatewayError as exc:
        raise ToolError(str(exc)) from exc


#: Read-only tools, in the order an agent would naturally reach for them.
READ_TOOLS = (
    list_workspaces,
    list_catalogs,
    list_schemas,
    list_tables,
    describe_table,
    get_query_result,
    search_semantic,
    get_semantic_model,
    query_metric,
    explain_metric,
)

ALL_TOOLS = (*READ_TOOLS, run_sql)
