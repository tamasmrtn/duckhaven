import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

import duckdb
import sqlglot
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi import Query as QueryParam
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.config import settings
from api.deps import get_current_user, get_db, get_polaris_client
from api.models.agent import Agent
from api.models.query import Query, SavedQuery
from api.models.user import User
from api.models.worksheet import Worksheet
from api.schemas.page import Page
from api.schemas.query import (
    ColumnSchemaOut,
    DescribeCreate,
    DescribeOut,
    QueryCreate,
    QueryOut,
    RowsPageOut,
    SavedQueryCreate,
    SavedQueryOut,
    SavedQueryUpdate,
    SqlMetadataOut,
)
from api.services import query as query_service
from api.services import query_history, query_submit, result_stream, sql_params
from api.services import sql_metadata as sql_metadata_service
from api.services.agent_access import (
    assert_agent_use,
    assert_can_assign_agent,
)
from api.services.agent_dispatch import is_agent_connected
from api.services.grants import GrantDenied
from api.services.paging import paginate
from api.services.permissions import Permission
from api.services.polaris import PolarisClient
from api.services.rbac import has_permission
from api.services.runtimes import (
    AgentNotDispatchable,
    check_dispatchable,
)
from api.services.sql_classify import STATEMENT_TYPES, classify_statement
from api.services.sql_guard import SQLNotAllowed, assert_allowed
from api.services.sql_sessions import service as sql_session_service
from api.services.sql_sessions.client_info import parse_user_agent
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/workspaces/{workspace}/queries",
    status_code=202,
    response_model=QueryOut,
    responses={200: {"model": QueryOut, "description": "Finished within `wait_timeout_s`."}},
)
async def create_query(
    ws: Annotated[str, Path(alias="workspace")],
    body: QueryCreate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> Query:
    """Submit SQL for execution. Accepted, not completed: 202 with a query id.

    Dispatch picks a connected agent the caller may use, so 503 means no
    compatible compute is available rather than that the SQL was wrong. Poll
    `GET /queries/{query_id}` for status and read results from
    `GET /queries/{query_id}/rows`.

    A read whose tables are unchanged since an identical query ran is answered
    from the result cache instead: the 202 then carries a query that is already
    `done`, with `cache_status` "hit". Send `use_cache: false` to always run.

    Send `wait_timeout_s` to block until the run finishes, up to that many seconds:
    the answer is then 200 with the finished query, or 202 if it is still going."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)
    # Committed before any wait, which re-reads the row.
    query = await query_submit.submit_query(
        db,
        workspace,
        body,
        user,
        polaris,
        client=parse_user_agent(request.headers.get("user-agent")),
    )
    if body.wait_timeout_s is not None:
        if body.wait_timeout_s > 0 and query.status in ("queued", "running"):
            await sql_session_service.await_query_done(db, query, body.wait_timeout_s)
        if query.status not in ("queued", "running"):
            response.status_code = status.HTTP_200_OK
    return query


async def _assert_result_access(db: AsyncSession, query: Query, user: User) -> None:
    """403 unless the caller's grants still cover what ``query`` read."""
    try:
        await query_service.assert_result_access(db, query, user.id)
    except GrantDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "grant_denied", "detail": str(exc)},
        ) from exc


# Statements DuckDB types as queries that describe or list rather than compute a
# result; `DESCRIBE` over them does not parse.
_NOT_DESCRIBABLE = frozenset({"DESCRIBE", "DESC", "SHOW", "SUMMARIZE", "PRAGMA"})


@router.post(
    "/workspaces/{workspace}/queries/describe",
    response_model=DescribeOut,
    responses={
        422: {"description": "Not one describable statement, or it does not bind."},
        504: {"description": "The agent did not answer within the wait."},
    },
)
async def describe_query(
    ws: Annotated[str, Path(alias="workspace")],
    body: DescribeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DescribeOut:
    """A query's result columns and their DuckDB types, without running it.

    The statement is bound by an agent (`DESCRIBE`), never executed, so it reads
    no rows, and the caller needs only the `metadata` tier on the tables it names.
    With no agent connected and elastic compute on, compute is started and the
    call waits for it, answering 503 `compute_starting` if it is not up in time.
    Statements that return no grid (DML, DDL) answer with `columns: null` without
    touching an agent."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)

    try:
        statements = duckdb.extract_statements(body.sql)
    except duckdb.Error as exc:
        raise _not_describable(f"The SQL does not parse: {exc}") from exc
    if len(statements) != 1:
        # A PIVOT without an IN list counts too: DuckDB plans it as a CREATE TYPE
        # (which reads the pivoted values) followed by the query.
        raise _not_describable(
            "Describe takes exactly one statement. A PIVOT without an IN list is two."
        )
    names = sorted(statements[0].named_parameters, key=lambda n: (not n.isdigit(), n.zfill(9)))
    parameters = body.parameters
    if parameters is None and names:
        # No values yet (a driver describing before it binds): NULL stands in.
        parameters = (
            [None] * len(names) if all(n.isdigit() for n in names) else dict.fromkeys(names)
        )
    sql = sql_params.render_for_request(body.sql, parameters)
    try:
        assert_allowed(sql)
    except SQLNotAllowed as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "sql_not_allowed", "detail": str(exc)},
        ) from exc

    statement_type = classify_statement(sql)
    if statements[0].type != duckdb.StatementType.SELECT:
        return DescribeOut(statement_type=statement_type, columns=None, parameter_names=names)
    first = sqlglot.tokenize(sql, read="duckdb")[0].text.upper()
    if first in _NOT_DESCRIBABLE:
        raise _not_describable(f"{first} statements cannot be described.")

    agent = None
    if body.agent_id is not None:
        agent = await db.get(Agent, body.agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        await assert_agent_use(db, user, agent, workspace)
        if not await is_agent_connected(db, agent.id):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "unavailable", "detail": "Agent not connected"},
            )
    wait_s = (
        settings.sql_session_wait_timeout_s if body.wait_timeout_s is None else body.wait_timeout_s
    )
    try:
        query = await query_service.run_internal_query(
            db,
            workspace=workspace,
            user_id=user.id,
            sql=f"DESCRIBE {sql}",
            origin="describe",
            active_catalog=body.catalog,
            agent=agent,
            wait_s=wait_s,
        )
    except GrantDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "grant_denied", "detail": str(exc)},
        ) from exc
    except AgentNotDispatchable as exc:
        raise query_submit.not_dispatchable(exc) from None
    except query_service.ComputeStarting as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "compute_starting", "detail": str(exc)},
            headers={"Retry-After": "5"},
        ) from exc
    except query_service.DefaultComputeUnavailable as exc:
        raise query_service.compute_unavailable_error(exc.decision) from exc
    except query_service.ComputeQueueFull as exc:
        raise query_service.compute_queue_full_error(exc.scope) from exc
    except query_service.AgentUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "unavailable", "detail": str(exc)},
        ) from exc

    if query.status == "failed":
        raise _not_describable(query.error or "The statement does not bind.")
    if query.status != "done":
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"error": "upstream_timeout", "detail": "The agent did not answer in time."},
        )
    page = await query_service.fetch_result_page(db, query, limit=1000)
    return DescribeOut(
        statement_type=statement_type,
        columns=[
            ColumnSchemaOut(name=row["column_name"], type=row["column_type"]) for row in page.rows
        ],
        parameter_names=names,
    )


def _not_describable(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": "not_describable", "detail": detail},
    )


@router.get("/workspaces/{workspace}/sql-metadata", response_model=SqlMetadataOut)
async def get_sql_metadata(
    ws: Annotated[str, Path(alias="workspace")],
    agent_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SqlMetadataOut:
    """DuckDB function/keyword/type dictionary for editor autocomplete.

    Sourced live from a connected agent (cached per DuckDB version). Pass
    ``agent_id`` — the worksheet's selected agent — to get *that* agent's
    dictionary: agents on different runtimes run different DuckDB versions, with
    different functions. Returns 503 when no compatible agent is connected so the
    editor falls back to its static keyword list rather than caching an empty
    dictionary.
    """
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)

    if agent_id is not None:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        await assert_agent_use(db, user, agent, workspace)
        try:
            await check_dispatchable(agent, await resolve_workspace_catalogs(db, workspace.id))
        except AgentNotDispatchable:
            agent = None
        if agent is not None and not await is_agent_connected(db, agent.id):
            agent = None
    else:
        agent = await query_service.pick_agent_for(db, workspace, principal_id=user.id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No compatible agent is connected.",
        )
    try:
        return await sql_metadata_service.fetch_metadata(db, workspace, agent, user.id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Could not load SQL metadata from the agent.",
        ) from exc


@router.get("/workspaces/{workspace}/queries", response_model=Page[QueryOut])
async def list_workspace_queries(
    ws: Annotated[str, Path(alias="workspace")],
    all_workspaces: bool = QueryParam(default=False),
    user_id: uuid.UUID | None = QueryParam(default=None),
    agent_id: uuid.UUID | None = QueryParam(default=None),
    since: datetime | None = QueryParam(default=None),
    until: datetime | None = QueryParam(default=None),
    origin: str | None = QueryParam(default=None),
    session_id: uuid.UUID | None = QueryParam(default=None),
    q: str | None = QueryParam(default=None),
    query_id: str | None = QueryParam(default=None),
    status_in: list[str] | None = QueryParam(default=None, alias="status"),
    statement_type: list[str] | None = QueryParam(default=None),
    slower_than_ms: int | None = QueryParam(default=None, ge=0),
    sort: query_history.SortKey = QueryParam(default="started_at"),
    dir: query_history.SortDir = QueryParam(default="desc"),
    cursor: str | None = QueryParam(default=None),
    limit: int = QueryParam(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[QueryOut]:
    """Query log, newest first. Doubles as the admin audit trail.

    Paged with a keyset cursor rather than an offset, because history is written
    to continuously and an offset page would duplicate and skip rows under load
    (see :mod:`api.services.query_history`). Sorting and filtering happen here,
    over the whole result set, before the page is cut — a client that sorted the
    page it was handed would be sorting a hundred rows out of thousands.

    A member sees their own workspace, gated on membership, and may narrow it
    however they like: by agent, kind of run, session, statement text, id,
    status, duration, statement type, and time. None of those reveal a row the
    list would not already have shown them.

    The cross-principal filters stay admin-only: ``all_workspaces``, and a
    ``user_id`` other than the caller's own. Filtering to *yourself* is not
    cross-principal, so it is open to anyone — it is how the default History
    view scopes itself. ``user_id`` matches the runs the assistant made for that
    person as well as their own.
    """
    is_admin = await has_permission(db, user, Permission.QUERIES_ADMIN)

    # Cross-workspace first: this branch skips the membership check below, so
    # nothing that follows may run before it has been refused.
    if all_workspaces:
        if not is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        workspace = None
    else:
        workspace = await get_workspace(db, ws)
        if workspace is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        await assert_workspace_member(db, workspace.id, user.id)

    if user_id is not None and user_id != user.id and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    if status_in is not None:
        unknown = sorted(set(status_in) - query_history.QUERY_STATUSES)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown status: {', '.join(unknown)}",
            )
    if statement_type is not None:
        unknown = sorted(set(statement_type) - STATEMENT_TYPES)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown statement type: {', '.join(unknown)}",
            )

    # The sort key is selected alongside the row so the cursor is built from the
    # value the database computed, not one recomputed in Python.
    sort_value = query_history.duration_expr() if sort == "duration" else Query.started_at

    # Left-join the user so History can show who ran each query, and who it was
    # run for (the names are attached to each Query row below for QueryOut
    # serialization).
    delegator = aliased(User)
    stmt = (
        select(Query, User.name, delegator.name, sort_value)
        .outerjoin(User, Query.user_id == User.id)
        .outerjoin(delegator, Query.on_behalf_of_id == delegator.id)
        .where(
            or_(
                Query.origin.is_(None),
                Query.origin.notin_(query_history.HIDDEN_ORIGINS),
            )
        )
        .order_by(*query_history.order_by(sort, dir))
    )

    if workspace is not None:
        stmt = stmt.where(Query.workspace_id == workspace.id)

    if origin is not None:
        # Interactive runs are stored with a null origin (the column only tags
        # non-interactive ones), so the filter spells that case explicitly rather
        # than leaving the UI unable to ask for it.
        stmt = stmt.where(
            Query.origin.is_(None) if origin == "interactive" else Query.origin == origin
        )
    if session_id is not None:
        stmt = stmt.where(Query.session_id == session_id)
    if agent_id is not None:
        # Open to any workspace member. By this point the statement is already
        # scoped to their own workspace (all_workspaces is admin-only and 403s
        # above), so this reveals nothing a member could not already see.
        stmt = stmt.where(Query.agent_id == agent_id)
    if user_id is not None:
        # A person's own runs include the ones the assistant made for them.
        stmt = stmt.where(or_(Query.user_id == user_id, Query.on_behalf_of_id == user_id))
    if since is not None:
        stmt = stmt.where(Query.started_at >= since)
    if until is not None:
        stmt = stmt.where(Query.started_at <= until)
    if q:
        stmt = stmt.where(query_history.search_predicate(q))
    if query_id:
        try:
            # Composed as one more predicate, never as a short-circuit: knowing
            # an id must not be a way around the workspace scoping above.
            stmt = stmt.where(query_history.id_predicate(query_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
    if status_in:
        stmt = stmt.where(Query.status.in_(status_in))
    if statement_type:
        # Rows classified as unknown (null) drop out here, and only here: with
        # the filter off they stay visible, because "we never classified this"
        # is not the same as "this is not a SELECT".
        stmt = stmt.where(Query.statement_type.in_(statement_type))
    if slower_than_ms is not None:
        stmt = stmt.where(query_history.duration_expr() >= slower_than_ms)

    if cursor:
        try:
            anchor_value, anchor_id = query_history.decode_cursor(cursor, sort)
        except query_history.InvalidCursor as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        stmt = stmt.where(query_history.keyset_predicate(sort, dir, anchor_value, anchor_id))

    # One more than asked for: its presence is what `has_more` reports, and it
    # costs a row rather than the COUNT(*) a total would.
    result = await db.execute(stmt.limit(limit + 1))
    rows = result.all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    queries: list[Query] = []
    for query, user_name, on_behalf_of_name, _value in rows:
        query.user_name = user_name
        query.on_behalf_of_name = on_behalf_of_name
        queries.append(query)
    next_cursor = (
        query_history.encode_cursor(rows[-1][0].id, rows[-1][3]) if has_more and rows else None
    )
    return Page[QueryOut](items=queries, cursor=next_cursor, has_more=has_more)


@router.get("/queries/{query_id}", response_model=QueryOut)
async def get_query(
    query_id: uuid.UUID,
    wait_timeout_s: float = QueryParam(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Query:
    """One query's status, timings and error, by its global id.

    Addressed globally rather than under its workspace: the id is unique, and the
    submitter already holds it. Any member of the query's workspace may read it,
    which is what makes the history an audit trail.

    Pass `wait_timeout_s` to hold the response until the query reaches a terminal
    state, for a client following a statement that outlived the wait on submit.
    It is **opt-in and defaults to 0** deliberately: this is also the route the
    worksheet refreshes several times a second and the history reads, and neither
    wants a held request. Capped by `SQL_STATEMENT_MAX_WAIT_TIMEOUT_S`."""
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    if wait_timeout_s > 0:
        if wait_timeout_s > settings.sql_statement_max_wait_timeout_s:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"wait_timeout_s must be at most {settings.sql_statement_max_wait_timeout_s}"
                ),
            )
        await sql_session_service.await_query_done(db, query, wait_timeout_s)
    return query


@router.get("/queries/{query_id}/profile")
async def get_query_profile(
    query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict | None:
    """The normalized post-execution profile, or null when none was captured.

    Kept off ``QueryOut`` so list/history stay lean; the worksheet fetches it on
    demand when a query is done. A result-cache hit executed nothing, so it
    answers with the profile of the run whose result it served
    (``result_source_query_id``)."""
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    profile = query.profile
    if profile is None and query.result_source_query_id is not None:
        source = await db.get(Query, query.result_source_query_id)
        profile = source.profile if source is not None else None
    if profile is not None:
        # A profile can carry values from the data (dynamic filter bounds), so it
        # is guarded like the rows it was captured with.
        await _assert_result_access(db, query, user)
    return profile


@router.delete("/queries/{query_id}", status_code=204)
async def cancel_query(
    query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Ask the agent to stop a running query.

    Best effort and idempotent: cancelling a query that already finished is a
    no-op, not an error. The query row survives -- cancellation is a terminal
    status, not a deletion."""
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    await query_service.cancel_query(db, query)


@router.get("/queries/{query_id}/rows", response_model=RowsPageOut)
async def get_query_rows(
    query_id: uuid.UUID,
    # Capped like every other paged endpoint: uncapped, one request could ask
    # for the whole result set and defeat the paging this route exists to do.
    limit: int = QueryParam(default=100, ge=1, le=1000),
    cursor: str | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RowsPageOut:
    """A page of a finished query's result rows, with their column types.

    A result grid rather than a resource collection, which is why the envelope is
    `rows`/`columns` rather than `items`. Page with the opaque `cursor`."""
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    if query.status != "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Query not done")
    # DDL/DML statements complete without a result file. Report an empty page
    # rather than a 404 so the UI shows a clean "ran, no rows" state.
    if query.result_path is None:
        return RowsPageOut(rows=[], columns=[], cursor=None, total=0, column_schema=None)
    # A result-cache hit has no agent of its own; its rows come from its source.
    if query.agent_id is None and query.result_source_query_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No results available")
    await _assert_result_access(db, query, user)

    trace.get_current_span().set_attribute(
        "duckhaven.result_schema", query.result_schema is not None
    )
    return await query_service.fetch_result_page(
        db, query, limit=limit, offset=int(cursor) if cursor and cursor.isdigit() else 0
    )


@router.get(
    "/queries/{query_id}/result",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "The whole result, as an Arrow IPC stream or a Parquet file.",
            "content": {
                result_stream.ARROW_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}},
                result_stream.PARQUET_MEDIA_TYPE: {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
        },
        204: {"description": "The statement produced no result grid (DDL or DML)."},
        410: {"description": "The result file has been swept by retention."},
        502: {"description": "The agent holding the result answered with an error."},
    },
)
async def get_query_result(
    query_id: uuid.UUID,
    format: Annotated[
        Literal["arrow", "parquet"],
        QueryParam(description="`arrow` for an Arrow IPC stream, `parquet` for the file as is."),
    ] = "arrow",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """A finished query's whole result in a columnar format.

    Exact where JSON is not: every value keeps its type (DECIMAL as decimal,
    64-bit and wider integers as integers), and the result streams in one response
    instead of 1000-row pages. `column_schema` on the query names each column's
    DuckDB type. Guarded exactly like `/rows`."""
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    if query.status != "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Query not done")
    if query.result_path is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if query.agent_id is None and query.result_source_query_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No results available")
    await _assert_result_access(db, query, user)

    source = await query_service.resolve_result_source(db, query)
    if format == "parquet":
        body, headers = await result_stream.open_parquet(source)
        media_type = result_stream.PARQUET_MEDIA_TYPE
    else:
        body, headers = await result_stream.open_arrow(source), {}
        media_type = result_stream.ARROW_MEDIA_TYPE
    # The body streams after this returns, and FastAPI keeps the request's database
    # session open until the response is sent: end its transaction now so a long
    # download does not hold a pooled connection.
    await db.commit()
    return StreamingResponse(body, media_type=media_type, headers=headers)


@router.get("/workspaces/{workspace}/saved-queries", response_model=Page[SavedQueryOut])
async def list_saved_queries(
    ws: Annotated[str, Path(alias="workspace")],
    limit: int = QueryParam(default=100, ge=1, le=1000),
    cursor: str | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Page[SavedQueryOut]:
    """The workspace's saved queries, newest first, with who saved and last edited each.

    Shared, not per-user: a saved query belongs to the workspace, so any member
    sees all of them."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    # Join the creator and the last editor so the list can attribute both.
    editor = aliased(User)
    rows, next_cursor, has_more = await paginate(
        db,
        select(SavedQuery, User.name, editor.name)
        .join(User, SavedQuery.created_by == User.id)
        .join(editor, SavedQuery.updated_by == editor.id)
        .where(SavedQuery.workspace_id == workspace.id),
        sort=[SavedQuery.created_at.desc(), SavedQuery.id.desc()],
        limit=limit,
        cursor=cursor,
    )
    return Page[SavedQueryOut](
        items=[
            SavedQueryOut.model_validate(sq).model_copy(
                update={"created_by_name": name, "updated_by_name": editor_name}
            )
            for sq, name, editor_name in rows
        ],
        cursor=next_cursor,
        has_more=has_more,
    )


async def _saved_query_named(
    db: AsyncSession, workspace_id: uuid.UUID, name: str, *, exclude: uuid.UUID | None = None
) -> SavedQuery | None:
    """The saved query already using `name` in this workspace, ignoring case."""
    stmt = select(SavedQuery).where(
        SavedQuery.workspace_id == workspace_id,
        func.lower(SavedQuery.name) == name.lower(),
    )
    if exclude is not None:
        stmt = stmt.where(SavedQuery.id != exclude)
    return (await db.execute(stmt)).scalar_one_or_none()


def _is_name_clash(exc: IntegrityError) -> bool:
    """Whether the write failed on the per-workspace unique name, and not on
    some other constraint that a 409 "name taken" would misreport."""
    return "uq_saved_queries_ws_lower_name" in str(exc.orig) or (
        # SQLite names the columns rather than the index.
        "saved_queries.workspace_id" in str(exc.orig) and "UNIQUE" in str(exc.orig)
    )


def _name_taken(existing: SavedQuery | None, name: str) -> HTTPException:
    detail: dict[str, object] = {
        "error": "saved_query_exists",
        "detail": f"A saved query named '{name}' already exists in this workspace",
    }
    if existing is not None:
        detail.update(id=str(existing.id), name=existing.name)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post(
    "/workspaces/{workspace}/saved-queries",
    status_code=201,
    responses={
        200: {"description": "A saved query with this name existed; its SQL was replaced."},
        201: {"description": "A new saved query was created."},
    },
    response_model=SavedQueryOut,
)
async def create_saved_query(
    ws: Annotated[str, Path(alias="workspace")],
    body: SavedQueryCreate,
    response: Response,
    on_conflict: Literal["replace", "error"] = QueryParam(
        default="replace",
        description=(
            "What to do when a saved query with this name (ignoring case) exists: "
            "`replace` its SQL and default agent, or fail with 409 `saved_query_exists`."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedQuery:
    """Save SQL under a name.

    By default an existing query with the same name (ignoring case) is replaced
    rather than duplicated: 201 when a query was created, 200 when one was
    replaced. Pass `on_conflict=error` to get a 409 instead, so a client can ask
    before overwriting a query someone else shares. Requires `writer`."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    # A default agent is a live dispatch path (the scheduler falls back to it), so
    # it needs the same `use` tier as choosing the agent on a schedule.
    await assert_can_assign_agent(db, user, body.default_agent_id, workspace=workspace)
    sq = await _saved_query_named(db, workspace.id, body.name)
    if sq is not None:
        if on_conflict == "error":
            raise _name_taken(sq, body.name)
        # Replacing keeps the creator for attribution; the editor becomes the
        # principal scheduled runs execute as.
        sq.name = body.name
        sq.sql = body.sql
        sq.default_agent_id = body.default_agent_id
        sq.updated_by = user.id
        sq.updated_at = datetime.now(UTC)
        response.status_code = status.HTTP_200_OK
    else:
        now = datetime.now(UTC)
        sq = SavedQuery(
            workspace_id=workspace.id,
            name=body.name,
            sql=body.sql,
            default_agent_id=body.default_agent_id,
            created_by=user.id,
            created_at=now,
            updated_by=user.id,
            updated_at=now,
        )
        db.add(sq)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Another save claimed the name between the lookup and the commit. Any
        # other integrity failure is not a naming problem, so it is not a 409.
        await db.rollback()
        if not _is_name_clash(exc):
            raise
        raise _name_taken(None, body.name) from None
    await db.refresh(sq)
    return sq


@router.patch(
    "/workspaces/{workspace}/saved-queries/{saved_query_id}", response_model=SavedQueryOut
)
async def update_saved_query(
    ws: Annotated[str, Path(alias="workspace")],
    saved_query_id: uuid.UUID,
    body: SavedQueryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedQuery:
    """Change a saved query's name, SQL or default agent. Requires `writer`.

    A partial update: omitted fields are left alone. Changing the SQL or the
    default agent makes the caller the query's editor, which is the principal its
    scheduled runs execute as. Renaming onto another query's name is a 409."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == saved_query_id,
            SavedQuery.workspace_id == workspace.id,
        )
    )
    sq = result.scalar_one_or_none()
    if sq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved query not found")
    fields = body.model_dump(exclude_unset=True)
    if "default_agent_id" in fields:
        await assert_can_assign_agent(db, user, fields["default_agent_id"], workspace=workspace)
    if fields.get("name") is not None:
        clash = await _saved_query_named(db, workspace.id, fields["name"], exclude=sq.id)
        if clash is not None:
            raise _name_taken(clash, fields["name"])
    for key, value in fields.items():
        setattr(sq, key, value)
    if "sql" in fields or "default_agent_id" in fields:
        sq.updated_by = user.id
    sq.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if not _is_name_clash(exc):
            raise
        raise _name_taken(None, fields.get("name") or sq.name) from None
    await db.refresh(sq)
    return sq


@router.delete("/workspaces/{workspace}/saved-queries/{saved_query_id}", status_code=204)
async def delete_saved_query(
    ws: Annotated[str, Path(alias="workspace")],
    saved_query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a saved query. Requires `writer`.

    Schedules that referenced it are not silently orphaned -- see the schedules
    router for how a scheduled saved query is retired."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == saved_query_id,
            SavedQuery.workspace_id == workspace.id,
        )
    )
    sq = result.scalar_one_or_none()
    if sq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved query not found")
    # Worksheets linked to it keep their SQL and become plain drafts. Done here as
    # well as by the FK's ON DELETE SET NULL, which SQLite does not enforce.
    await db.execute(
        update(Worksheet).where(Worksheet.saved_query_id == sq.id).values(saved_query_id=None)
    )
    await db.delete(sq)
    await db.commit()
