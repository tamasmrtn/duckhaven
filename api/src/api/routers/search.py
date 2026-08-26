"""Cross-catalog search for the command palette (⌘K).

Fans out over every catalog attached to the workspace, matching schema and
table names by substring and reusing the exact grant redaction the
schema/table list endpoints already apply (`schemas.py`'s `list_schemas` /
`list_tables`) so a scoped-catalog grant can't be bypassed by searching
instead of browsing. Also matches saved-query names. Deliberately narrow —
this is the palette's data source, not a general-purpose search framework.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.query import SavedQuery
from api.models.user import User
from api.schemas.search import SearchResultOut
from api.services import grants as grant_service
from api.services.polaris import PolarisClient, PolarisError
from api.services.workspace import (
    assert_workspace_member,
    get_workspace,
    resolve_workspace_catalogs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces")

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


def _escape_like(s: str) -> str:
    """Escape LIKE/ILIKE metacharacters so a literal name search (e.g. a saved
    query named "daily_report") can't act as an accidental wildcard."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/{workspace}/search", response_model=list[SearchResultOut])
async def search_workspace(
    ws: Annotated[str, Path(alias="workspace")],
    q: str = Query(""),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> list[SearchResultOut]:
    """Find catalogs, schemas, tables and saved queries by name across the workspace.

    Prefix and substring matching over names only, not contents. Results are
    filtered by grant, so a caller never sees an object they could not open, and
    an empty `q` returns nothing rather than everything."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)

    needle = q.strip().lower()
    if not needle:
        return []

    catalogs = await resolve_workspace_catalogs(db, workspace.id)

    # The Polaris calls below touch only the HTTP client, so they can run
    # concurrently; the grant checks further down share `db`, a single
    # AsyncSession that is not safe for concurrent use, so those stay
    # sequential. return_exceptions=True also isolates a catalog whose Polaris
    # namespace is stale/missing from the rest of the search, instead of one
    # PolarisError aborting the whole request.
    schemas_per_catalog = await asyncio.gather(
        *(polaris.list_schemas(cat.polaris_name) for cat in catalogs),
        return_exceptions=True,
    )

    live = []
    for cat, schemas in zip(catalogs, schemas_per_catalog):
        if isinstance(schemas, PolarisError):
            logger.warning("Search skipped catalog=%s: %s", cat.slug, schemas)
            continue
        if isinstance(schemas, BaseException):
            raise schemas
        live.append((cat, schemas))

    schema_lookup = [(cat, s) for cat, schemas in live for s in schemas]
    tables_per_schema = await asyncio.gather(
        *(polaris.list_tables(cat.polaris_name, s.name) for cat, s in schema_lookup),
        return_exceptions=True,
    )
    tables_by_schema = {}
    for (cat, s), tables in zip(schema_lookup, tables_per_schema):
        if isinstance(tables, PolarisError):
            logger.warning("Search skipped schema=%s.%s: %s", cat.slug, s.name, tables)
            tables_by_schema[(cat.slug, s.name)] = []
            continue
        if isinstance(tables, BaseException):
            raise tables
        tables_by_schema[(cat.slug, s.name)] = tables

    results: list[SearchResultOut] = []
    for cat, schemas in live:
        scoped = await grant_service.is_scoped(db, workspace.id, cat)

        matched_schemas = [s for s in schemas if needle in s.name.lower()]
        if scoped and matched_schemas:
            visible = await grant_service.visible_schemas(
                db, workspace.id, cat, user.id, [s.name for s in matched_schemas]
            )
            matched_schemas = [s for s in matched_schemas if s.name in visible]
        for s in matched_schemas:
            results.append(SearchResultOut(type="schema", catalog=cat.slug, name=s.name))

        for s in schemas:
            tables = tables_by_schema[(cat.slug, s.name)]
            matched_tables = [t for t in tables if needle in t.name.lower()]
            if not matched_tables:
                continue
            if scoped:
                visible = await grant_service.visible_tables(
                    db, workspace.id, cat, user.id, s.name, [t.name for t in matched_tables]
                )
                matched_tables = [t for t in matched_tables if t.name in visible]
            for t in matched_tables:
                results.append(
                    SearchResultOut(type="table", catalog=cat.slug, schema_name=s.name, name=t.name)
                )

    if len(results) < limit:
        sq_result = await db.execute(
            select(SavedQuery)
            .where(
                SavedQuery.workspace_id == workspace.id,
                SavedQuery.name.ilike(f"%{_escape_like(q.strip())}%", escape="\\"),
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

    return results[:limit]
