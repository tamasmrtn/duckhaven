"""Answer an agent's CATALOG_REQUEST: a catalog a running statement turned out to need.

Dispatch sends the catalogs a statement names (see `catalog_refs`). A view or
macro can read another one, which only surfaces when DuckDB binds it, so the
agent asks for that catalog here instead of having been given every catalog's
credentials up front.

Every request is authorized afresh, and only against the agent's own work:
the statement must be one this agent is running, the catalog must be bound to
that statement's workspace, and a scoped catalog needs the principal to be able
to read **all** of it. The grant check before dispatch could not see this
catalog, so it never checked an object in it, and the request does not say
which object the view reads.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.query import Query, SavedQuery, Schedule
from api.services.grants import TIER_SCALE, is_scoped, node_tier, tier_rank
from api.services.session_credentials import build_catalog_attach
from api.services.workspace import resolve_workspace_catalogs

logger = logging.getLogger(__name__)


async def _grant_principal(db: AsyncSession, query: Query) -> uuid.UUID | None:
    """Whose grants the statement runs under, as its dispatch decided.

    A scheduled run has no user; dispatch checks it against whoever last edited
    the saved query, and so does this.
    """
    if query.user_id is not None or query.schedule_id is None:
        return query.user_id
    schedule = await db.get(Schedule, query.schedule_id)
    if schedule is None or schedule.saved_query_id is None:
        return None
    saved = await db.get(SavedQuery, schedule.saved_query_id)
    return saved.updated_by if saved is not None else None


async def answer_catalog_request(
    db: AsyncSession, agent_id: uuid.UUID, payload: dict
) -> dict[str, object]:
    """The CATALOG_RESPONSE payload: the attach descriptor, or why there is none."""
    request_id = payload.get("request_id")
    slug = str(payload.get("catalog") or "").lower()

    def refuse(reason: str) -> dict[str, object]:
        logger.info("Refused catalog %r to agent %s: %s", slug, agent_id, reason)
        return {"request_id": request_id, "catalog": None, "error": reason}

    try:
        query_id = uuid.UUID(str(payload.get("query_id")))
    except ValueError:
        return refuse("unknown statement")
    query = await db.get(Query, query_id)
    if query is None or query.agent_id != agent_id or query.status not in ("queued", "running"):
        return refuse("unknown statement")

    catalogs = await resolve_workspace_catalogs(db, query.workspace_id)
    catalog = next((c for c in catalogs if c.slug.lower() == slug), None)
    if catalog is None:
        return refuse("not a catalog of this workspace")
    if await is_scoped(db, query.workspace_id, catalog):
        principal = await _grant_principal(db, query)
        tier = (
            await node_tier(db, query.workspace_id, catalog, principal, None, None)
            if principal is not None
            else None
        )
        if tier_rank(tier) < TIER_SCALE["reader"]:
            return refuse(
                f"catalog {catalog.slug} is scoped: reach it only by naming its tables, "
                "or with a grant on the whole catalog"
            )
    return {"request_id": request_id, "catalog": await build_catalog_attach(catalog)}
