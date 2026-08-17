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


async def search_semantic(ctx: RunContext[AssistantDeps], query: str) -> dict:
    """Find the curated metrics and dimensions a question is about.

    Call this FIRST for any question about a business measure — revenue, orders,
    customers, conversion, churn — before browsing the catalog. Curated
    definitions are authoritative: when one exists you must use it rather than
    working the calculation out yourself from column names.

    Returns ranked matches, each naming the model it belongs to. When
    ``ambiguous`` is non-empty, more than one authoritative metric matches the
    words used equally well and they mean different things — ask the user which
    they meant instead of picking one.

    Args:
        query: The user's question, or the business term to look up.
    """
    try:
        return await ctx.deps.gateway.search_semantic(query)
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def get_semantic_model(ctx: RunContext[AssistantDeps], model: str) -> dict:
    """Read one semantic model: its metrics, dimensions, datasets and joins.

    Use after ``search_semantic`` tells you which model is relevant, to see what
    else can be asked of it — which dimensions exist, which time grains a date
    supports, and what each metric actually computes.

    Args:
        model: The model slug, from ``search_semantic``.
    """
    try:
        return await ctx.deps.gateway.get_semantic_model(model)
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def query_metric(
    ctx: RunContext[AssistantDeps],
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

    Prefer this over ``run_sql`` whenever a curated metric covers the question.
    The SQL is generated from the stored definitions — the aggregation, its
    filters, the join path and the correct date column — so the answer matches
    what the organization has agreed these words mean. You do not write the
    aggregation; you choose the concepts.

    Args:
        model: Model slug from ``search_semantic``.
        metrics: Metric names to compute. All must belong to the same dataset.
        dimensions: Dimension names to group by.
        grain: Time grain to group by — one of day, week, month, quarter, year.
        time_window: The period to restrict to. State it explicitly, because
            "last month" is ambiguous. One of:
            ``{"kind": "last_complete", "grain": "month", "n": 1}`` — the last N
            *complete* periods, excluding the one in progress (this is what
            "last month" usually means);
            ``{"kind": "trailing", "grain": "day", "n": 30}`` — a rolling window
            of N periods ending today, including today ("the last 30 days");
            ``{"kind": "to_date", "grain": "year"}`` — period start through today;
            ``{"kind": "absolute", "start": "2026-01-01", "end": "2026-02-01"}`` —
            explicit dates, end exclusive.
        filters: Predicates on dimensions, e.g.
            ``[{"dimension": "country", "op": "in", "values": ["United States"]}]``.
            Operators: eq, ne, in, not_in, gt, gte, lt, lte, contains, is_null,
            is_not_null. Match values against the dimension's sample values.
        order_by: e.g. ``[{"field": "revenue", "descending": true}]``. Fields must
            be metrics or dimensions in the result.
        limit: Maximum rows.
    """
    body: dict = {"model": model, "metrics": metrics}
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

    try:
        compiled = await ctx.deps.gateway.compile_metric_query(body)
    except GatewayError as exc:
        # The compiler refuses rather than approximating, and its messages name
        # the legal alternatives — so handing this straight back lets the model
        # correct itself or ask, instead of falling back to inventing SQL.
        raise ModelRetry(str(exc)) from exc

    try:
        result = await ctx.deps.gateway.run_sql(
            compiled["sql"], catalog=ctx.deps.catalog, timeout_s=ctx.deps.query_timeout_s
        )
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc

    # Compiled SQL never re-triggers the "you should have used a metric" nudge.
    result.pop("semantic_warning", None)
    result["sql"] = compiled["sql"]
    result["definitions_used"] = compiled.get("definitions_used", [])
    if compiled.get("warnings"):
        result["notes"] = compiled["warnings"]
    return result


async def explain_metric(ctx: RunContext[AssistantDeps], model: str, metric: str) -> dict:
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
        return await ctx.deps.gateway.metric_definition(model, metric)
    except GatewayError as exc:
        raise ModelRetry(str(exc)) from exc


async def get_worksheet_sql(ctx: RunContext[AssistantDeps]) -> str:
    """Return the SQL currently in the user's worksheet editor.

    Call this before proposing an edit so you build on what the user already has.
    Returns an empty-editor note when nothing is open.
    """
    return ctx.deps.editor_sql or "(the worksheet editor is empty or not open)"


async def get_worksheet_selection(ctx: RunContext[AssistantDeps]) -> str:
    """Return the user's current text selection in the worksheet editor, if any.

    Call this before proposing an edit when the user's request sounds like it's
    about a specific part of the query ("this WHERE clause", "just this line").
    If they have a selection, propose_sql_edit should replace only that fragment
    instead of the whole worksheet. Returns a no-selection note otherwise.
    """
    return ctx.deps.selection_sql or "(no text is currently selected)"


async def propose_sql_edit(ctx: RunContext[AssistantDeps], sql: str, explanation: str) -> str:
    """Propose replacing SQL in the user's worksheet editor.

    Use this when the user asks you to write, fix, or change the SQL in their
    editor. The proposed SQL is shown in their editor as a highlighted change that
    they accept or reject — it is not executed.

    If get_worksheet_selection returned a non-empty selection, provide only the
    replacement text for that selected fragment — do not repeat the rest of the
    worksheet. Otherwise, provide the complete new SQL for the whole worksheet.

    Args:
        sql: The proposed SQL — a replacement for the selection if one exists,
            otherwise the complete new SQL for the worksheet.
        explanation: A one-line summary of what changed and why.
    """
    return "Proposed the edit in the user's editor; they will accept or reject it."


ALL_TOOLS = [
    search_semantic,
    get_semantic_model,
    query_metric,
    explain_metric,
    list_catalogs,
    list_schemas,
    list_tables,
    describe_table,
    run_sql,
    get_query_result,
    get_worksheet_sql,
    get_worksheet_selection,
    propose_sql_edit,
]
