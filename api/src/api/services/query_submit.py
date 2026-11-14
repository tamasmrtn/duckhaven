"""Submitting one statement as a query, as ``POST /workspaces/{workspace}/queries`` does.

Lifted out of the router so other callers can submit the same way: a dashboard
runs each widget's query through exactly the checks, cache lookup, placement and
dispatch a worksheet's run goes through. The only difference a caller may make
is ``origin``, which says what started the run (``None`` for a person's own,
``"dashboard"`` for a dashboard widget's).

Who the run is for is the ``user`` passed in. When the request is the
assistant's, the person it acts for is bound to ``db`` (``services.delegation``),
and every check below holds for both.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query, SavedQuery
from api.models.user import User
from api.models.workspace import Workspace
from api.schemas.query import QueryCreate
from api.services import query as query_service
from api.services import sql_params
from api.services.agent_access import assert_agent_tier, assert_agent_use
from api.services.agent_dispatch import is_agent_connected, send_to_agent
from api.services.assistant.identity import ASSISTANT_EMAIL
from api.services.compute import service as compute_service
from api.services.grants import GrantDenied
from api.services.migration.service import workspace_has_active_migration
from api.services.polaris import PolarisClient
from api.services.result_cache import service as result_cache
from api.services.runtimes import (
    AgentNotDispatchable,
    RuntimeRetired,
    assert_restartable,
    check_dispatchable,
)
from api.services.sql_guard import SQLNotAllowed, assert_allowed, is_read_only
from api.services.workspace import (
    DEFAULT_SCHEMA,
    get_default_catalog,
    resolve_workspace_catalogs,
)
from duckhaven_shared.concurrency import parse_set_concurrency
from duckhaven_shared.protocol import Frame, FrameType

logger = logging.getLogger(__name__)

#: What may start a run through here. Both are interactive: someone is waiting.
Origin = Literal["dashboard"] | None


async def submit_query(
    db: AsyncSession,
    workspace: Workspace,
    body: QueryCreate,
    user: User,
    polaris: PolarisClient,
    *,
    origin: Origin = None,
    client: tuple[str | None, str | None] = (None, None),
) -> Query:
    """Submit ``body`` as ``user``, who the caller has checked is a member of ``workspace``.

    Returns the query as it stands: answered from the cache, dispatched, or parked
    for compute. Refusals raise the same ``HTTPException`` the REST API answers
    with. ``client`` is the ``(name, version)`` of the client that sent it, and
    ``body.tags`` are stamped on the run. Commits."""
    try:
        query = await _submit(db, workspace, body, user, polaris, origin)
        # Stamped once here rather than on each way a run can start.
        query.client_name, query.client_version = client
        query.tags = body.tags
        await db.commit()
    except IntegrityError:
        await query_service.refuse_raced_query_id(db, body.id)
        raise
    return query


async def _submit(
    db: AsyncSession,
    workspace: Workspace,
    body: QueryCreate,
    user: User,
    polaris: PolarisClient,
    origin: Origin,
) -> Query:
    await query_service.refuse_taken_query_id(db, body.id)
    # Values for placeholders become literals here, before any check reads the SQL:
    # from this line on the statement is exactly the one that runs.
    body.sql = sql_params.render_for_request(body.sql, body.parameters)

    # `SET duckhaven_concurrency = '<profile>'` is a DuckHaven control command,
    # not DuckDB SQL (sql_guard rejects SET): intercept it before the guard,
    # retune the selected agent's admission, and record a done query for audit.
    try:
        profile = parse_set_concurrency(body.sql)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "sql_not_allowed", "detail": str(exc)},
        ) from exc
    if profile is not None:
        return await _set_concurrency(db, workspace.id, user, body, profile)

    try:
        assert_allowed(body.sql)
    except SQLNotAllowed as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "sql_not_allowed", "detail": str(exc)},
        ) from exc

    # Reject writes while a catalog attached to this workspace is mid storage
    # migration (the read-only window). Reads are unaffected.
    if not is_read_only(body.sql) and await workspace_has_active_migration(db, workspace.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "catalog_read_only",
                "detail": "A storage backend migration is in progress; the catalog is read-only.",
            },
        )

    # No specific agent chosen. With elastic compute, dispatch to a compatible
    # connected agent if one exists, else park the run queued and provision one.
    # Without it, pick the connected agent the server would choose — default
    # runtime first, never a beta one — so a caller that doesn't care which agent
    # runs its SQL (the assistant, a script) needn't pick one itself.
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    if body.agent_id is None:
        # Decided once: the cache lookup and the dispatch below act on the same answer.
        # The assistant never adds an agent to the pool, whatever the request says.
        allow_scale_out = body.scale_out and user.email != ASSISTANT_EMAIL
        decision = await query_service.resolve_compute(
            db, workspace, principal_id=user.id, allow_scale_out=allow_scale_out
        )
        cache = await _cache_lookup(
            db, workspace, catalogs, body, decision.agent, polaris, origin=origin
        )
        if cache.hit:
            return await _serve_cached(
                db, workspace, user, body, cache, catalogs, polaris, origin=origin
            )
        if decision.via_default and decision.placement is not query_service.Placement.UNAVAILABLE:
            # The workspace default: run it through the named-agent path below, which
            # already knows how to start a stopped elastic agent or wait for one that
            # is starting. Its members need no grant of their own on it.
            agent = decision.agent
            body.agent_id = agent.id
        elif decision.placement is query_service.Placement.UNAVAILABLE:
            raise await _fail_unplaced(db, workspace, user, body, decision, origin=origin)
        elif settings.elastic_compute_enabled:
            return await _create_elastic_query(
                db,
                workspace,
                user.id,
                body,
                cache,
                agent=decision.agent,
                allow_scale_out=allow_scale_out,
                origin=origin,
            )
        else:
            agent = decision.agent
            body.agent_id = agent.id
    else:
        result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        # Before the connectivity probe, so a caller without access learns nothing
        # about the agent's state (and an invisible agent 404s exactly like a
        # missing one).
        await assert_agent_use(db, user, agent, workspace)
        # Before the connectivity probe too: a cached result needs no agent, so a
        # hit is answered even while the chosen agent is down or starting.
        cache = await _cache_lookup(db, workspace, catalogs, body, agent, polaris, origin=origin)
        if cache.hit:
            return await _serve_cached(
                db, workspace, user, body, cache, catalogs, polaris, origin=origin
            )
    if not await is_agent_connected(db, body.agent_id):
        if (
            settings.elastic_compute_enabled
            and agent.provider is not None
            and agent.lifecycle in ("terminated", "failed")
        ):
            return await _create_starting_query(
                db, workspace, user.id, body, agent, cache, origin=origin
            )
        if (
            settings.elastic_compute_enabled
            and agent.provider is not None
            and agent.lifecycle == "provisioning"
        ):
            # Someone else just started it: join that start rather than failing, and
            # do not start it twice.
            return await _create_starting_query(
                db, workspace, user.id, body, agent, cache, origin=origin, restart=False
            )
        failed = Query(
            id=body.id,
            workspace_id=workspace.id,
            agent_id=agent.id,
            user_id=user.id,
            sql=body.sql,
            origin=origin,
            timeout_s=body.timeout_s,
            active_catalog=body.catalog,
            active_schema=body.schema_name,
        )
        db.add(failed)
        raise await _agent_not_connected(db, failed)

    # Checked here, before a query row exists, so a refusal leaves nothing behind.
    # dispatch_query applies the same check on every other path.
    try:
        await check_dispatchable(agent, catalogs, use_schema=body.schema_name is not None)
    except AgentNotDispatchable as exc:
        raise not_dispatchable(exc) from None

    await _stamp_saved_query_run(db, workspace, body.saved_query_id)

    query = Query(
        id=body.id,
        workspace_id=workspace.id,
        agent_id=body.agent_id,
        user_id=user.id,
        sql=body.sql,
        origin=origin,
    )
    cache.stamp(query)
    db.add(query)
    await db.flush()
    try:
        await query_service.dispatch_query(
            db,
            query,
            timeout_s=body.timeout_s,
            active_catalog=body.catalog,
            active_schema=body.schema_name,
        )
    except GrantDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "grant_denied", "detail": str(exc)},
        ) from exc
    except query_service.AgentUnavailable:
        # Presence is read with a TTL, so the agent can lose its socket between the
        # probe above and the send. Same answer as the probe, rather than a 500.
        raise await _agent_not_connected(db, query) from None
    return query


async def _cache_lookup(
    db: AsyncSession,
    workspace,
    catalogs,
    body: QueryCreate,
    agent: Agent | None,
    polaris: PolarisClient,
    *,
    origin: Origin,
) -> result_cache.Lookup:
    """Whether the result cache can answer this run, in the context the chosen (or
    likely) agent would run it in: the catalog and schema it would `USE`, its
    runtime and its time zone."""
    if not catalogs:
        return result_cache.Lookup(status=result_cache.BYPASS, detail="no_catalogs")
    default = await get_default_catalog(db, workspace.id)
    current = body.catalog or (default.slug if default is not None else catalogs[0].slug)
    context = result_cache.context_for(
        agent, current_catalog=current, current_schema=body.schema_name or DEFAULT_SCHEMA
    )
    return await result_cache.lookup(
        db,
        workspace=workspace,
        catalogs=catalogs,
        sql=body.sql,
        origin=origin,
        use_cache=body.use_cache,
        context=context,
        polaris=polaris,
    )


async def _serve_cached(
    db: AsyncSession,
    workspace,
    user: User,
    body: QueryCreate,
    cache: result_cache.Lookup,
    catalogs,
    polaris: PolarisClient,
    *,
    origin: Origin,
) -> Query:
    """Record a run answered by the result cache: a finished query row that points
    at the run whose result it reuses. Nothing is dispatched."""
    await _stamp_saved_query_run(db, workspace, body.saved_query_id)
    query = Query(
        id=body.id,
        workspace_id=workspace.id,
        user_id=user.id,
        sql=body.sql,
        origin=origin,
        timeout_s=body.timeout_s,
        active_catalog=body.catalog,
    )
    try:
        return await result_cache.serve_hit(
            db, cache, query, principal_id=user.id, catalogs=catalogs, polaris=polaris
        )
    except GrantDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "grant_denied", "detail": str(exc)},
        ) from exc


def not_dispatchable(exc: AgentNotDispatchable) -> HTTPException:
    """The 422 for an agent the dispatch checks refuse (extensions or runtime)."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": exc.code, "detail": exc.detail},
    )


def _runtime_retired(exc: RuntimeRetired) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "runtime_retired", "detail": str(exc)},
    )


async def _agent_not_connected(db: AsyncSession, query: Query) -> HTTPException:
    """Record `query` as a failed run and build the 503 for an unreachable agent.

    The run is committed before the error is raised so History shows the attempt
    -- otherwise a worksheet reports a failure that leaves no trace anywhere.
    """
    query.status = "failed"
    query.error = "Agent not connected"
    query.finished_at = datetime.now(UTC)
    await db.commit()
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "unavailable",
            "detail": "Agent not connected",
            "query_id": str(query.id),
        },
    )


async def _fail_unplaced(
    db: AsyncSession,
    workspace,
    user: User,
    body: QueryCreate,
    decision: query_service.ComputeDecision,
    *,
    origin: Origin,
) -> HTTPException:
    """Record a run nothing could take as failed, and build the error for it.

    Committed before the error is raised so History shows the attempt, as for an
    unreachable named agent. ``agent_required`` (elastic off, nothing connected, no
    default) is the long-standing answer to a caller that named no agent and leaves
    no row behind, so it is returned as it always was.
    """
    error = query_service.compute_unavailable_error(decision)
    if error.detail["error"] == "agent_required":
        return error
    failed = Query(
        id=body.id,
        workspace_id=workspace.id,
        agent_id=decision.agent.id if decision.agent is not None else None,
        user_id=user.id,
        sql=body.sql,
        origin=origin,
        timeout_s=body.timeout_s,
        active_catalog=body.catalog,
        status="failed",
        error=query_service.unavailable_message(decision),
        finished_at=datetime.now(UTC),
    )
    db.add(failed)
    await db.commit()
    error.detail["query_id"] = str(failed.id)
    return error


async def _assert_queue_has_room(
    db: AsyncSession, workspace, user_id: uuid.UUID, body: QueryCreate, *, origin: Origin
) -> None:
    """Refuse to park a run when too many already wait for compute.

    Recorded as a failed run, committed before the error is raised, so History shows
    the attempt as it does for any other run that found no compute.
    """
    try:
        await compute_service.assert_parked_capacity(db, workspace.id)
    except query_service.ComputeQueueFull as exc:
        failed = Query(
            id=body.id,
            workspace_id=workspace.id,
            user_id=user_id,
            sql=body.sql,
            origin=origin,
            timeout_s=body.timeout_s,
            active_catalog=body.catalog,
            status="failed",
            error=str(exc),
            finished_at=datetime.now(UTC),
        )
        db.add(failed)
        await db.commit()
        error = query_service.compute_queue_full_error(exc.scope)
        error.detail["query_id"] = str(failed.id)
        raise error from None


async def _stamp_saved_query_run(db: AsyncSession, workspace, saved_query_id) -> None:
    """Record that a saved query was just run.

    Shared by both create paths. The elastic path did not do this, so a saved
    query only ever run against the pool reported "never run" in the UI while its
    runs sat in History. A missing or foreign id is ignored, so a run never fails
    over a saved query someone deleted.
    """
    if saved_query_id is None:
        return
    saved = (
        await db.execute(
            select(SavedQuery).where(
                SavedQuery.id == saved_query_id,
                SavedQuery.workspace_id == workspace.id,
            )
        )
    ).scalar_one_or_none()
    if saved is not None:
        saved.last_run_at = datetime.now(UTC)


async def _create_starting_query(
    db: AsyncSession,
    workspace,
    user_id: uuid.UUID,
    body: QueryCreate,
    agent: Agent,
    cache: result_cache.Lookup,
    *,
    origin: Origin,
    restart: bool = True,
) -> Query:
    """Park a run for a named elastic agent and start that agent.

    With ``restart=False`` the agent is already starting (another caller began it), so
    the run only parks and the same binder picks it up when the agent registers.

    The mirror of ``_create_elastic_query`` for an explicit target. The run cannot
    be re-routed to whichever pool agent happens to be up — the caller chose this
    one — so it parks ``queued`` with ``agent_id`` NULL and ``requested_agent_id``
    set, and ``compute.service.bind_targeted_work`` dispatches it when the agent
    dials home. Failing instead would make an idle-terminated agent permanently
    unusable, because the reaper tears it down precisely *because* nothing is using
    it — the reasoning the scheduler already applies to an unattended run.

    ``origin`` is the caller's: null for an interactive run, which History must not
    report as anything else. Compatibility is checked when the agent registers, not
    here: a row that failed while provisioning advertises no capabilities at all.
    Only a retired runtime is refused up front, since it can never start again.
    """
    try:
        assert_restartable(agent)
    except RuntimeRetired as exc:
        raise _runtime_retired(exc) from None
    await _assert_queue_has_room(db, workspace, user_id, body, origin=origin)
    await _stamp_saved_query_run(db, workspace, body.saved_query_id)
    query = Query(
        id=body.id,
        workspace_id=workspace.id,
        agent_id=None,
        requested_agent_id=agent.id,
        user_id=user_id,
        sql=body.sql,
        status="queued",
        origin=origin,
        # Recorded for the same reason as a parked pool run: the dispatch happens
        # outside this request and would otherwise fall back to the workspace
        # default catalog and timeout.
        timeout_s=body.timeout_s,
        active_catalog=body.catalog,
        active_schema=body.schema_name,
    )
    cache.stamp(query)
    db.add(query)
    # Commit before provisioning: on Docker the agent can register within a second,
    # and the binder can only claim a row it can see.
    await db.commit()

    if not restart:
        logger.info("Query %s waiting for agent %s, which is already starting", query.id, agent.id)
        return query
    if await compute_service.restart_elastic_agent(db, agent) is None:
        query.status = "failed"
        query.error = "Could not start the configured agent."
        query.finished_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "compute_unavailable",
                "detail": "Could not start the configured agent.",
            },
            headers={"Retry-After": "5"},
        )
    logger.info("Query %s starting terminated agent %s", query.id, agent.id)
    return query


async def _create_elastic_query(
    db: AsyncSession,
    workspace,
    user_id: uuid.UUID,
    body: QueryCreate,
    cache: result_cache.Lookup,
    *,
    agent: Agent | None,
    origin: Origin,
    allow_scale_out: bool = True,
) -> Query:
    """Run against the elastic pool: dispatch now if a compatible agent is up,
    otherwise park the run ``queued`` and provision one (bound on registration).

    ``agent`` is what the caller's decision picked, scoped to agents it may use (or
    omitting `agent_id` would be a way around a denial on a specific agent). It is
    not picked again here: a second pick could disagree with the first and with the
    cache lookup made from it. With ``allow_scale_out`` false nothing is provisioned.
    """
    if agent is None:
        await _assert_queue_has_room(db, workspace, user_id, body, origin=origin)
    await _stamp_saved_query_run(db, workspace, body.saved_query_id)
    query = Query(
        id=body.id,
        workspace_id=workspace.id,
        agent_id=agent.id if agent is not None else None,
        user_id=user_id,
        sql=body.sql,
        status="queued",
        # An interactive pool run is `elastic`; a dashboard's keeps its own origin,
        # which the binder and the reaper also look for.
        origin=origin or "elastic",
        # Recorded because this run may be dispatched long after this request: a run
        # parked during a cold start is replayed by compute.service.bind_queued_work,
        # which has no access to the request that created it. Without these the replay
        # used the workspace default catalog and the default timeout, so unqualified
        # table names resolved somewhere the user had not chosen.
        timeout_s=body.timeout_s,
        active_catalog=body.catalog,
        active_schema=body.schema_name,
    )
    cache.stamp(query)
    db.add(query)
    await db.flush()

    if agent is not None:
        try:
            await query_service.dispatch_query(
                db,
                query,
                timeout_s=body.timeout_s,
                active_catalog=body.catalog,
                active_schema=body.schema_name,
            )
        except GrantDenied as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "grant_denied", "detail": str(exc)},
            ) from exc
        except AgentNotDispatchable as exc:
            raise not_dispatchable(exc) from None
        except query_service.AgentUnavailable:
            # Presence is read from Postgres with a TTL, so the agent picked above can
            # have lost its socket already -- and a terminating agent keeps its
            # ownership row until the container actually goes away. For a pool run that
            # is not a failure: unbind and fall through to scale-out, exactly as if no
            # agent had been available. Raising here would surface as a 500.
            logger.info("Elastic pool agent %s was unavailable; provisioning", agent.id)
            query.agent_id = None
        else:
            return query

    if not allow_scale_out:
        decision = query_service.ComputeDecision(
            query_service.Placement.UNAVAILABLE, reason="no_compute"
        )
        query.status = "failed"
        query.error = query_service.unavailable_message(decision)
        query.finished_at = datetime.now(UTC)
        await db.commit()
        error = query_service.compute_unavailable_error(decision)
        error.detail["query_id"] = str(query.id)
        raise error

    # No compatible agent connected: coalesced scale-out. The run stays queued
    # (agent_id NULL) until the provisioned agent registers and binds it.
    pool_key = await compute_service.resolve_pool_key(db, workspace)
    await compute_service.ensure_agent(db, pool_key)
    await db.commit()
    return query


async def _set_concurrency(
    db: AsyncSession, workspace_id: uuid.UUID, user: User, body: QueryCreate, profile: str
) -> Query:
    """Apply a concurrency `SET` to the selected agent and log it as a done query.

    Agent-global: it retunes admission for every query on that agent, not just
    this user's. The agent owns the profile (held in memory, reset on restart).

    That fleet-wide blast radius is why this needs `operate` rather than `use`:
    retuning admission changes how the agent serves everyone on it, which is a
    lifecycle-grade act, not a dispatch.
    """
    result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    await assert_agent_tier(db, user, agent, "operate")
    if not await is_agent_connected(db, body.agent_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent not connected"
        )
    frame = Frame(type=FrameType.SET_CONCURRENCY, payload={"profile": profile})
    await send_to_agent(db, body.agent_id, frame.model_dump_json())
    query = Query(
        id=body.id,
        workspace_id=workspace_id,
        agent_id=body.agent_id,
        user_id=user.id,
        sql=body.sql,
        status="done",
        row_count=0,
        finished_at=datetime.now(tz=UTC),
    )
    db.add(query)
    await db.commit()
    await db.refresh(query)
    return query
