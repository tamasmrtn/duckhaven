"""Agent runtimes from the control plane's side.

A runtime (duckhaven_shared.runtimes) is a curated agent image: a DuckDB line plus a
fixed set of extensions. Runtimes are a property of compute. An elastic agent is
provisioned as the runtime it was created with (``Agent.requested_runtime_id``); a
static agent is whatever image its operator runs. Every agent reports its runtime in
``capabilities`` once it connects, and that report is what work is routed on.

This module answers four questions:

- **Which image** starts a runtime (``image_for``).
- **What an agent really is** (``resolve``): an agent reporting a runtime this
  release doesn't know, a DuckDB line its runtime doesn't have, or a runtime other
  than the one it was provisioned as is not trusted with work. Curated runtimes
  only: an unrecognised image is shown, but nothing is sent to it.
- **Whether work may go to it** (``assert_dispatchable``), which also carries the
  existing extension check, so every dispatch path applies the same rules.
- **How to rank it** when nobody named an agent (``auto_pick_rank``): the default
  runtime first, other generally available ones next, deprecated last. A beta
  runtime is never picked automatically — only work that names its agent goes to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from api.config import settings
from api.services.agent_capabilities import agent_supports_catalog, missing_extension
from duckhaven_shared import runtimes
from duckhaven_shared.runtimes import Runtime

if TYPE_CHECKING:
    from api.models.agent import Agent

# pending: nothing reported yet. ok: a curated runtime, on its DuckDB line.
# inferred: an image from before runtimes, matched to one by its DuckDB line.
# mismatch: an elastic agent running something other than what it was started as.
# unrecognized: not a curated runtime, or not on that runtime's DuckDB line.
# retired: a curated runtime that is no longer served.
RuntimeState = Literal["pending", "ok", "inferred", "mismatch", "unrecognized", "retired"]
DISPATCHABLE_STATES = frozenset({"ok", "inferred"})


class AgentNotDispatchable(ValueError):
    """Work may not be sent to this agent. ``code`` is the API error code.

    A ``ValueError`` like the other dispatch refusals, so the scheduler's and the
    maintenance scanner's existing handling fails the one run and moves on.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class RuntimeRetired(ValueError):
    """An elastic agent's runtime is retired, so it cannot be started again."""

    def __init__(self, runtime_id: str) -> None:
        super().__init__(
            f"Runtime {runtime_id} is retired and can no longer be started; "
            "create compute on a current runtime instead."
        )
        self.runtime_id = runtime_id


@dataclass(frozen=True)
class AgentRuntime:
    runtime: Runtime | None
    state: RuntimeState


def image_for(runtime_id: str) -> str:
    """The agent image for a runtime, as this release publishes it."""
    if runtime_id == settings.default_runtime and settings.agent_image:
        return settings.agent_image
    tag = settings.agent_image_tag or (
        "latest" if settings.app_version in ("", "0.0.0-dev") else settings.app_version
    )
    return f"{settings.agent_image_repository}:{tag}-duckdb{runtime_id}"


def assert_restartable(agent: Agent) -> Runtime:
    """The runtime an elastic agent restarts as; ``RuntimeRetired`` if it can't.

    Checked before anything is parked on the restart, so a caller hears "retired"
    rather than a generic failure after the fact.
    """
    runtime_id = agent.requested_runtime_id or settings.default_runtime
    runtime = runtimes.get(runtime_id)
    if runtime is None or runtime.status == "retired":
        raise RuntimeRetired(runtime_id)
    return runtime


def resolve(agent: Agent) -> AgentRuntime:
    """What runtime an agent is, judged from what it reported."""
    caps = agent.capabilities or {}
    requested = runtimes.get(agent.requested_runtime_id)
    if not caps:
        return AgentRuntime(requested, "pending")

    engine_line = runtimes.engine_line(caps.get("engine_version") or caps.get("duckdb_version"))
    reported_id = caps.get("runtime_id")
    if reported_id is None:
        runtime = runtimes.infer(caps.get("engine_version") or caps.get("duckdb_version"))
        state: RuntimeState = "inferred"
    else:
        runtime = runtimes.get(reported_id)
        state = "ok"
    if runtime is None or runtime.duckdb_line != engine_line:
        return AgentRuntime(runtime, "unrecognized")
    if agent.requested_runtime_id is not None and agent.requested_runtime_id != runtime.id:
        return AgentRuntime(runtime, "mismatch")
    if runtime.status == "retired":
        return AgentRuntime(runtime, "retired")
    return AgentRuntime(runtime, state)


def runtime_id_of(agent: Agent) -> str | None:
    """The runtime to stamp on work sent to this agent."""
    runtime = resolve(agent).runtime
    return runtime.id if runtime is not None else None


def assert_dispatchable(
    agent: Agent,
    catalogs: list,
    *,
    for_session: bool = False,
) -> None:
    """Raise ``AgentNotDispatchable`` unless this agent may run work for these catalogs.

    ``catalogs`` are the workspace's bound catalogs, every one of which is attached
    on each query, so the agent must serve every catalog's kind on its backend.
    A session additionally needs a configuration lock that really applies: it runs
    under a relaxed statement policy on the strength of it. An agent that doesn't
    report its sandbox (older images) is taken as before.
    """
    resolved = resolve(agent)
    if resolved.state == "retired":
        raise AgentNotDispatchable(
            "runtime_retired",
            f"Agent '{agent.name}' runs {resolved.runtime.display_name}, which is retired.",
        )
    if resolved.state not in DISPATCHABLE_STATES and resolved.state != "pending":
        raise AgentNotDispatchable(
            "runtime_unsupported",
            f"Agent '{agent.name}' is not running a supported runtime "
            f"({_describe(agent, resolved)}).",
        )
    for catalog in catalogs:
        kind = catalog.storage_backend.kind
        if not agent_supports_catalog(agent.capabilities, catalog.kind, kind):
            missing = missing_extension(agent.capabilities, catalog.kind, kind)
            raise AgentNotDispatchable(
                "agent_incompatible",
                f"Agent '{agent.name}' is missing the '{missing}' extension required "
                f"by catalog '{catalog.slug}' ({catalog.kind} on {kind}).",
            )
    if for_session and (agent.capabilities or {}).get("sandbox") == "failed":
        raise AgentNotDispatchable(
            "agent_sandbox_unverified",
            f"Agent '{agent.name}' could not lock its DuckDB configuration, "
            "so it cannot hold a SQL session.",
        )


def auto_pick_rank(agent: Agent) -> int | None:
    """Sort key for choosing an agent nobody named, lowest first; None = never."""
    resolved = resolve(agent)
    if resolved.state not in DISPATCHABLE_STATES or resolved.runtime is None:
        return None
    if resolved.runtime.id == settings.default_runtime:
        return 0
    return {"ga": 1, "deprecated": 2}.get(resolved.runtime.status)


def _describe(agent: Agent, resolved: AgentRuntime) -> str:
    caps = agent.capabilities or {}
    reported = caps.get("runtime_id") or "none"
    engine = caps.get("engine_version") or caps.get("duckdb_version") or "unknown"
    if resolved.state == "mismatch":
        return f"started as {agent.requested_runtime_id}, reports {reported} on DuckDB {engine}"
    return f"reports runtime {reported} on DuckDB {engine}"
