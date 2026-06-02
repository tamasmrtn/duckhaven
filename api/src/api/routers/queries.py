import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.agent import Agent
from api.models.query import Query, SavedQuery
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.schemas.query import QueryCreate, QueryOut, RowsPageOut, SavedQueryCreate, SavedQueryOut
from api.services import query as query_service
from api.services.agent_capabilities import agent_supports_backend, required_extension
from api.services.agent_registry import registry
from api.services.sql_guard import SQLNotAllowed, assert_allowed
from api.services.workspace import assert_workspace_member, get_workspace

router = APIRouter()


@router.post("/workspaces/{ws}/queries", status_code=202, response_model=QueryOut)
async def create_query(
    ws: str,
    body: QueryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Query:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await assert_workspace_member(db, workspace.id, user.id)

    try:
        assert_allowed(body.sql)
    except SQLNotAllowed as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "sql_not_allowed", "detail": str(exc)},
        ) from exc

    result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if registry.get(body.agent_id) is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )

    backend = await db.get(StorageBackend, workspace.storage_backend_id)
    if backend is not None and not agent_supports_backend(agent.capabilities, backend.kind):
        ext = required_extension(backend.kind)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "agent_incompatible",
                "detail": (
                    f"Agent '{agent.name}' is missing the '{ext}' extension required "
                    f"by this workspace's {backend.kind} backend."
                ),
            },
        )

    query = Query(
        workspace_id=workspace.id,
        agent_id=body.agent_id,
        user_id=user.id,
        sql=body.sql,
    )
    db.add(query)
    await db.flush()
    await query_service.dispatch_query(
        db,
        query,
        memory_limit_gb=body.memory_limit_gb,
        timeout_s=body.timeout_s,
    )
    return query


@router.get("/queries/{query_id}", response_model=QueryOut)
async def get_query(
    query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Query:
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    return query


@router.delete("/queries/{query_id}", status_code=204)
async def cancel_query(
    query_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    await query_service.cancel_query(db, query)


@router.get("/queries/{query_id}/rows", response_model=RowsPageOut)
async def get_query_rows(
    query_id: uuid.UUID,
    limit: int = 100,
    cursor: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RowsPageOut:
    result = await db.execute(select(Query).where(Query.id == query_id))
    query = result.scalar_one_or_none()
    if query is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, query.workspace_id, user.id)
    if query.status != "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Query not done")
    if query.agent_id is None or query.result_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No results available")

    agent_result = await db.execute(select(Agent).where(Agent.id == query.agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None or agent.result_host is None or agent.result_port is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent result endpoint unavailable",
        )

    token = await query_service.agent_session_token(db, query.agent_id)

    offset = int(cursor) if cursor and cursor.isdigit() else 0
    upstream = await query_service.proxy_rows(agent, query, token=token)
    if upstream.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch results from agent"
        )
    rows, columns = query_service.decode_parquet_page(upstream.content, limit, offset)
    total = query.row_count or 0
    next_offset = offset + limit
    next_cursor = str(next_offset) if next_offset < total else None
    return RowsPageOut(rows=rows, columns=columns, cursor=next_cursor, total=total)


@router.get("/workspaces/{ws}/saved-queries", response_model=list[SavedQueryOut])
async def list_saved_queries(
    ws: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SavedQuery]:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    result = await db.execute(select(SavedQuery).where(SavedQuery.workspace_id == workspace.id))
    return list(result.scalars().all())


@router.post(
    "/workspaces/{ws}/saved-queries",
    status_code=201,
    response_model=SavedQueryOut,
)
async def create_saved_query(
    ws: str,
    body: SavedQueryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SavedQuery:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="writer")
    sq = SavedQuery(
        workspace_id=workspace.id,
        name=body.name,
        sql=body.sql,
        default_agent_id=body.default_agent_id,
        created_by=user.id,
    )
    db.add(sq)
    await db.commit()
    await db.refresh(sq)
    return sq
