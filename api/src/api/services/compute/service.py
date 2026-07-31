"""Scale-out: provision an elastic agent on demand, coalescing concurrent asks.

``ensure_agent`` is the scale-out primitive. It is safe to call from many requests
at once: a per-``pool_key`` Postgres advisory lock (the coalescing primitive used
throughout the repo) means concurrent callers that find no compatible agent
provision *one*, not one each.

The row is written (``lifecycle="provisioning"`` with a deterministic
``instance_id``) *before* the backend is asked to create the instance, so a crash
between the two leaves a reconcilable record — the reaper's leak sweep can always
tie a cloud instance back to a row (or terminate an orphan). Postgres is the
state-of-record (I9); the backend is reconciled to it.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.metrics import record_agent_provision
from api.models.agent import Agent
from api.models.query import Query, SavedQuery
from api.models.table_metadata import TableMetadata
from api.models.user import Credential
from api.models.workspace import Workspace
from api.services.agent_dispatch import disconnect_agent
from api.services.agent_telemetry import record_lifecycle_event
from api.services.compute.backends import ProvisionRequest, get_backend
from api.services.workspace import resolve_workspace_catalogs

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("duckhaven.api")

# Elastic lifecycle states that count as "supply already exists / on its way" so
# ensure_agent doesn't provision a duplicate.
_ACTIVE_LIFECYCLE = ("provisioning", "running")

BOOTSTRAP_TTL_HOURS = 24


async def resolve_pool_key(db: AsyncSession, workspace: Workspace) -> str:
    """The capability scope a workspace needs, as a stable string.

    An agent can serve the workspace iff it supports every backend kind across the
    workspace's attached catalogs (the same rule ``pick_agent_for`` matches on). We
    key the elastic pool on the sorted set of those kinds so one provisioned agent
    serves every workspace with the same storage shape.
    """
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    kinds = sorted({c.storage_backend.kind for c in catalogs}) or ["object_store"]
    return ",".join(kinds)


def _lock_key(pool_key: str) -> int:
    """A stable signed-64-bit advisory-lock key for a pool (pg needs a bigint)."""
    digest = hashlib.blake2b(pool_key.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _instance_id(agent_id: uuid.UUID) -> str:
    """A DNS-safe instance name for one provisioning attempt.

    Carries the agent id so an instance is recognisable, plus a per-attempt suffix
    so a restart never targets the name of the instance it is replacing. Deleting
    is not instant on a cloud backend, and reusing the name meant a restart soon
    after a terminate hit a group still in Deleting state: provisioning raised, the
    row was marked failed, and the caller saw 502 provision_failed for a transient
    collision.

    The suffix costs nothing in reconcilability. The row is committed with its
    instance_id *before* the backend is asked to create anything, so the leak sweep
    still matches a live instance to its row by reading the column -- it never
    recomputes this -- and an instance created after a crash carries the managed tag
    and is swept as an orphan either way.
    """
    return f"dh-agent-{agent_id.hex[:20]}-{secrets.token_hex(3)}"


async def _count_active(db: AsyncSession, pool_key: str) -> int:
    return (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(Agent)
            .where(
                Agent.provider.is_not(None),
                Agent.pool_key == pool_key,
                Agent.lifecycle.in_(_ACTIVE_LIFECYCLE),
            )
        )
    ).scalar_one()


async def ensure_agent(db: AsyncSession, pool_key: str) -> Agent | None:
    """Ensure at least one elastic agent is provisioning/running for ``pool_key``.

    Returns the newly-provisioned agent, or ``None`` when supply already exists or
    the per-pool cap is reached. Concurrent callers coalesce on the advisory lock,
    so exactly one provisions.
    """
    if not settings.elastic_compute_enabled:
        return None

    # Serialize the check-then-provision against other callers for this pool.
    if db.bind.dialect.name == "postgresql":
        await db.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(pool_key)})

    if await _count_active(db, pool_key) >= settings.elastic_max_agents_per_pool:
        return None

    return await _create_and_provision(
        db,
        name=f"elastic-{secrets.token_hex(4)}",
        pool_key=pool_key,
        cpu=settings.elastic_default_cpu,
        memory_gb=settings.elastic_default_memory_gb,
        idle_timeout_s=None,
    )


async def provision_elastic_agent(
    db: AsyncSession,
    *,
    name: str,
    cpu: float,
    memory_gb: float,
    idle_timeout_s: float | None = None,
    access_mode: str = "open",
) -> Agent | None:
    """Provision one elastic agent at an explicit size (admin-initiated).

    Unlike ``ensure_agent`` this is a deliberate action — no pool coalescing or cap
    — mirroring starting a Databricks cluster. The agent is not bound to a pool
    (``pool_key`` NULL); it serves interactive queries once it registers, and the
    idle reaper (using ``idle_timeout_s``, or the global default when None)
    auto-terminates it. Returns the agent, or ``None`` if elastic compute is
    disabled or provisioning failed.

    ``access_mode`` is applied to the row before the backend is asked for anything,
    so an agent created ``restricted`` is never briefly usable by everyone: it
    cannot register and pick up work in a window where the ACL says otherwise.
    """
    if not settings.elastic_compute_enabled:
        return None
    return await _create_and_provision(
        db,
        name=name,
        pool_key=None,
        cpu=cpu,
        memory_gb=memory_gb,
        idle_timeout_s=idle_timeout_s,
        access_mode=access_mode,
    )


async def terminate_agent(db: AsyncSession, agent: Agent, *, reason: str) -> None:
    """Scale an elastic agent in now: destroy its instance and mark it terminated.

    Shared by the idle reaper and the admin/worksheet "terminate" action. The
    backend call is best-effort — if it raises, the row is still marked so the leak
    sweep retries against the still-present instance next cycle."""
    agent.lifecycle = "terminating"
    record_lifecycle_event(db, agent.id, "terminating", reason=reason)
    await db.commit()
    if agent.instance_id:
        with contextlib.suppress(Exception):
            await get_backend(agent.provider).terminate(agent.instance_id)

    # Close the socket and give up ownership before marking the row terminated.
    # Deleting a container group is not instant, and until it completes the agent keeps
    # heartbeating -- which refreshes last_ping_at and re-asserts status="healthy" from
    # its AGENT_STATUS frames. Presence is read from those columns, so without this the
    # agent stays in connected_agent_ids for tens of seconds after termination: it is
    # still offered by the picker, still shown healthy, and a query dispatched to it is
    # either refused or sent to a container that is about to be destroyed, leaving the
    # run stuck. Mirrors what delete_agent already does.
    with contextlib.suppress(Exception):
        await disconnect_agent(db, agent.id)

    agent.lifecycle = "terminated"
    agent.status = "unavailable"
    agent.owner_id = None
    agent.owner_url = None
    agent.terminated_at = datetime.now(tz=UTC)
    # An agent terminated while still provisioning never consumed its token.
    await revoke_bootstrap_credentials(db, agent.id)
    record_lifecycle_event(db, agent.id, "terminated", reason=reason)
    await db.commit()
    logger.info("Terminated elastic agent %s (%s)", agent.id, reason)


async def delete_agent(db: AsyncSession, agent: Agent) -> None:
    """Permanently remove an agent row. Irreversible.

    Destroys a live elastic instance first, then clears the references that would
    otherwise block the delete (queries/saved-queries/table-metadata keep their
    rows but lose the agent link), and deletes the row. Credentials cascade;
    schedules and SQL sessions null their agent by FK.
    """
    if agent.provider is not None and agent.lifecycle in ("provisioning", "running", "terminating"):
        if agent.instance_id:
            with contextlib.suppress(Exception):
                await get_backend(agent.provider).terminate(agent.instance_id)
    with contextlib.suppress(Exception):
        await disconnect_agent(db, agent.id)

    # Null the RESTRICT-guarded references so the row can be deleted while keeping
    # the audit rows themselves (they just show an unknown agent afterwards).
    await db.execute(sa.update(Query).where(Query.agent_id == agent.id).values(agent_id=None))
    await db.execute(
        sa.update(SavedQuery)
        .where(SavedQuery.default_agent_id == agent.id)
        .values(default_agent_id=None)
    )
    await db.execute(
        sa.update(TableMetadata)
        .where(TableMetadata.last_write_agent_id == agent.id)
        .values(last_write_agent_id=None)
    )
    await db.delete(agent)
    await db.commit()
    logger.info("Deleted agent %s", agent.id)


async def restart_elastic_agent(db: AsyncSession, agent: Agent) -> Agent | None:
    """Re-provision a terminated/failed elastic agent, reusing its row.

    Restarting keeps the agent's identity (name, size, idle timeout) and gives it a
    fresh instance + bootstrap token. Returns the agent, or ``None`` if it is not a
    restartable elastic agent or elastic compute is disabled.
    """
    if not settings.elastic_compute_enabled or agent.provider is None:
        return None
    if agent.lifecycle not in ("terminated", "failed"):
        return None

    now = datetime.now(tz=UTC)
    agent.lifecycle = "provisioning"
    agent.status = "unavailable"
    agent.provisioned_at = now
    agent.terminated_at = None
    agent.last_active_at = None
    agent.instance_id = _instance_id(agent.id)
    record_lifecycle_event(db, agent.id, "provisioning", reason="restart")
    await db.commit()
    return await _mint_and_provision(
        db,
        agent,
        cpu=agent.requested_cpu or settings.elastic_default_cpu,
        memory_gb=agent.requested_memory_gb or settings.elastic_default_memory_gb,
    )


async def _create_and_provision(
    db: AsyncSession,
    *,
    name: str,
    pool_key: str | None,
    cpu: float,
    memory_gb: float,
    idle_timeout_s: float | None,
    access_mode: str = "open",
) -> Agent | None:
    """Write the agent row, then mint + provision it. The row (with a deterministic
    ``instance_id`` and the requested size) is committed *before* the backend call,
    so a crash mid-provision always leaves a reconcilable record — never a leak.

    ``access_mode`` defaults to ``open`` so pool scale-out (``ensure_agent``) keeps
    serving whoever triggered it; only the admin-initiated path passes anything else.
    """
    now = datetime.now(tz=UTC)
    agent = Agent(
        name=name,
        status="unavailable",
        provider=settings.elastic_provider,
        lifecycle="provisioning",
        pool_key=pool_key,
        requested_cpu=cpu,
        requested_memory_gb=memory_gb,
        idle_timeout_s=idle_timeout_s,
        provisioned_at=now,
        access_mode=access_mode,
    )
    db.add(agent)
    await db.flush()  # assign agent.id
    agent.instance_id = _instance_id(agent.id)
    record_lifecycle_event(db, agent.id, "provisioning")
    await db.commit()
    return await _mint_and_provision(db, agent, cpu=cpu, memory_gb=memory_gb)


async def revoke_bootstrap_credentials(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Delete any unused enrollment tokens for ``agent_id``. The caller commits.

    A bootstrap token is single-use and is consumed by successful registration, so
    one that still exists belongs to an attempt that never registered. Nothing else
    collects them: without this, a row that failed to provision leaves a token valid
    for BOOTSTRAP_TTL_HOURS, and every restart mints another alongside it.

    Revoking is also what keeps a failed row dead. A slow instance that dials home
    after the reaper gave up cannot register without a token, so the lifecycle guard
    in agents_ws is the second line rather than the only one.
    """
    await db.execute(
        sa.delete(Credential).where(
            Credential.agent_id == agent_id, Credential.kind == "agent_bootstrap"
        )
    )


async def _mint_and_provision(
    db: AsyncSession, agent: Agent, *, cpu: float, memory_gb: float
) -> Agent | None:
    """Mint a fresh bootstrap credential for ``agent`` and ask the backend to create
    its instance. Shared by first-time create and restart. On backend failure the
    row is marked ``failed`` (reconcilable), and ``None`` returned."""
    provider = settings.elastic_provider
    token = f"dh_boot_{secrets.token_urlsafe(16)}"
    # Clear any token left by an earlier attempt on this row before minting the
    # replacement, so a restarted agent carries exactly one live credential.
    await revoke_bootstrap_credentials(db, agent.id)
    db.add(
        Credential(
            user_id=None,
            agent_id=agent.id,
            kind="agent_bootstrap",
            token=token,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=BOOTSTRAP_TTL_HOURS),
        )
    )
    await db.commit()

    req = ProvisionRequest(
        instance_id=agent.instance_id,
        image=settings.agent_image,
        control_plane_url=settings.elastic_control_plane_url or "",
        bootstrap_token=token,
        cpu=cpu,
        memory_gb=memory_gb,
        tags={"duckhaven-managed": "true", "duckhaven-agent-id": str(agent.id)},
    )
    # A cold start is the largest unexplained gap a user can see: the query's own
    # dispatch span cannot begin until an agent exists. Making provisioning a span
    # of its own turns that gap into a labelled child in the same trace.
    started = time.monotonic()
    with tracer.start_as_current_span("provision_agent") as span:
        span.set_attribute("duckhaven.agent_id", str(agent.id))
        span.set_attribute("duckhaven.compute_provider", provider)
        span.set_attribute("duckhaven.requested_cpu", cpu)
        span.set_attribute("duckhaven.requested_memory_gb", memory_gb)
        try:
            await get_backend(provider).provision(req)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, "provision failed"))
            record_agent_provision(provider, "failure")
            logger.exception("Elastic provision failed for agent %s", agent.id)
            agent.lifecycle = "failed"
            agent.terminated_at = datetime.now(tz=UTC)
            await revoke_bootstrap_credentials(db, agent.id)
            record_lifecycle_event(db, agent.id, "failed", reason="provision_failed")
            await db.commit()
            return None

    record_agent_provision(provider, "success", time.monotonic() - started)
    logger.info(
        "Provisioned elastic agent %s (instance %s, size %svCPU/%sGiB)",
        agent.id,
        agent.instance_id,
        cpu,
        memory_gb,
    )
    return agent


async def resolve_result_host(agent: Agent) -> str | None:
    """Where the control plane should fetch ``agent``'s results, per its backend.

    Only meaningful for elastic agents: the control plane created the instance, so the
    cloud is the authority on its address. A subnet-injected instance cannot be told
    its own address (it is assigned after the container's configuration is fixed) and
    cannot be inferred from the connection either, because the agent dials home through
    a NAT gateway and so appears to come from the gateway.

    ``None`` for static agents, for backends with no address to report, and on any
    backend error — in each case the caller falls back to the connection's peer.
    """
    if agent.provider is None or not agent.instance_id:
        return None
    try:
        return await get_backend(agent.provider).address(agent.instance_id)
    except Exception:
        logger.exception("Could not resolve result host for agent %s", agent.id)
        return None


async def ensure_result_host(db: AsyncSession, agent: Agent) -> str | None:
    """Resolve and store an elastic agent's result address if it is not known yet.

    Registration can legitimately find nothing: the address is assigned after the
    container group is created, and ARM does not always report it by the time the agent
    dials home -- observed against a real deployment, where the agent registered about
    30 seconds in and the address appeared later. Asking again when the address is
    actually needed costs one read and is reliable, because by then the agent has
    accepted and finished work.
    """
    if agent.result_host:
        return agent.result_host
    host = await resolve_result_host(agent)
    if host:
        agent.result_host = host
        await db.commit()
        logger.info("Resolved result host %s for elastic agent %s", host, agent.id)
    return host


async def bind_queued_work(db: AsyncSession, agent: Agent) -> int:
    """Dispatch queued, agent-less elastic queries this agent can now serve.

    Called right after an elastic agent registers (dials home). A query that
    triggered scale-out was parked ``queued`` with ``agent_id=NULL`` and
    ``origin="elastic"``; now that a compatible agent is up, bind and dispatch each
    one whose workspace matches this agent's ``pool_key``. Returns the count bound.

    Failures are isolated per query (a bad dispatch fails that one run, not the
    batch) — mirrors the scheduler's per-schedule isolation.
    """
    if agent.pool_key is None:
        return 0

    # Lazy import breaks the query <-> compute import cycle at module load.
    from api.services.query import dispatch_query

    queued = (
        (
            await db.execute(
                sa.select(Query).where(
                    Query.agent_id.is_(None),
                    Query.origin == "elastic",
                    Query.status == "queued",
                )
            )
        )
        .scalars()
        .all()
    )

    bound = 0
    # One lookup per workspace rather than per query: every query for a workspace
    # resolves to the same pool key, and a cold start can park dozens of runs.
    pool_keys: dict[uuid.UUID, str | None] = {}
    for query in queued:
        if query.workspace_id not in pool_keys:
            workspace = await db.get(Workspace, query.workspace_id)
            pool_keys[query.workspace_id] = (
                await resolve_pool_key(db, workspace) if workspace is not None else None
            )
        if pool_keys[query.workspace_id] != agent.pool_key:
            continue

        # Claim the row before dispatching, and commit the claim so a concurrent
        # caller sees it. Dispatch is irreversible -- it runs the SQL on an agent --
        # so two callers reaching it for the same query execute a parked
        # `INSERT INTO ... SELECT` twice and duplicate data.
        #
        # The guard is the WHERE clause, not the read above: `agent_id IS NULL`
        # makes the claim atomic on any backend and across replicas, where a lock
        # around the check would only serialize callers sharing one database
        # session. A row someone else took reports rowcount 0 and is skipped.
        claimed = await db.execute(
            sa.update(Query)
            .where(Query.id == query.id, Query.agent_id.is_(None))
            .values(agent_id=agent.id)
        )
        if claimed.rowcount == 0:
            continue
        await db.commit()
        await db.refresh(query)

        try:
            # Replay what the requester actually asked for. These were recorded on the
            # row when the run was parked precisely because this dispatch happens
            # outside that request; passing neither meant a parked run silently used the
            # workspace default catalog and the default timeout.
            await dispatch_query(
                db,
                query,
                principal_id=query.user_id,
                active_catalog=query.active_catalog,
                **({} if query.timeout_s is None else {"timeout_s": query.timeout_s}),
            )
            bound += 1
        except Exception:
            logger.exception("Failed to bind queued query %s to agent %s", query.id, agent.id)
            query.status = "failed"
            query.error = "dispatch failed after provisioning"
            query.finished_at = datetime.now(tz=UTC)
            # Release the claim: this agent never ran it, so leaving itself recorded
            # would attribute the run to it forever in History and in the agent filter.
            query.agent_id = None
    await db.commit()
    if bound:
        logger.info("Bound %d queued queries to elastic agent %s", bound, agent.id)
    return bound


async def record_activity(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Stamp ``last_active_at`` when work is dispatched to an elastic agent.

    A no-op for static agents (``provider IS NULL``): their idle state is
    irrelevant and we never terminate them. The caller commits.
    """
    await db.execute(
        sa.update(Agent)
        .where(Agent.id == agent_id, Agent.provider.is_not(None))
        .values(last_active_at=datetime.now(tz=UTC))
    )
