"""Cross-catalog search for the command palette (⌘K).

Fans out over every catalog attached to the workspace, matching schema and
table names by substring and reusing the exact grant redaction the
schema/table list endpoints already apply (`schemas.py`'s `list_schemas` /
`list_tables`) so a scoped-catalog grant can't be bypassed by searching
instead of browsing. Also matches saved-query names. Deliberately narrow —
this is the palette's data source, not a general-purpose search framework.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.query import SavedQuery
from api.models.user import User
from api.schemas.search import SearchResultOut
from api.services import grants as grant_service
from api.services.polaris import PolarisClient
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)

router = APIRouter(prefix="/workspaces")

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


@router.get("/{ws}/search", response_model=list[SearchResultOut])
async def search_workspace(
    ws: str,
    q: str = Query(""),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> list[SearchResultOut]:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)

    needle = q.strip().lower()
    if not needle:
        return []

    results: list[SearchResultOut] = []

    for cat in await resolve_workspace_catalogs(db, workspace.id):
        if len(results) >= limit:
            break
        scoped = await grant_service.is_scoped(db, workspace.id, cat)
        schemas = await polaris.list_schemas(cat.polaris_name)

        matched_schemas = [s for s in schemas if needle in s.name.lower()]
        if scoped and matched_schemas:
            visible = await grant_service.visible_schemas(
                db, workspace.id, cat, user.id, [s.name for s in matched_schemas]
            )
            matched_schemas = [s for s in matched_schemas if s.name in visible]
        for s in matched_schemas:
            if len(results) >= limit:
                break
            results.append(SearchResultOut(type="schema", catalog=cat.slug, name=s.name))

        for s in schemas:
            if len(results) >= limit:
                break
            tables = await polaris.list_tables(cat.polaris_name, s.name)
            matched_tables = [t for t in tables if needle in t.name.lower()]
            if not matched_tables:
                continue
            if scoped:
                visible = await grant_service.visible_tables(
                    db, workspace.id, cat, user.id, s.name, [t.name for t in matched_tables]
                )
                matched_tables = [t for t in matched_tables if t.name in visible]
            for t in matched_tables:
                if len(results) >= limit:
                    break
                results.append(
                    SearchResultOut(type="table", catalog=cat.slug, schema_name=s.name, name=t.name)
                )

    if len(results) < limit:
        sq_result = await db.execute(
            select(SavedQuery)
            .where(
                SavedQuery.workspace_id == workspace.id,
                SavedQuery.name.ilike(f"%{q.strip()}%"),
            )
            .limit(limit - len(results))
        )
        for sq in sq_result.scalars().all():
            results.append(
                SearchResultOut(
                    type="saved_query",
                    name=sq.name,
                    id=sq.id,
                    sql=sq.sql,
                    default_agent_id=sq.default_agent_id,
                )
            )

    return results
