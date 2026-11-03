"""Where work that named no agent runs (``query.resolve_compute``).

A workspace with no default compute keeps the old behaviour exactly: auto-pick a
connected agent, else scale out the elastic pool when that is allowed. A workspace
with one is strict: the default, started if it is a stopped elastic agent, joined if
it is already starting, and an error otherwise. It never picks another agent and never
adds one to the pool.
"""

from __future__ import annotations

import json
import uuid

import pytest
from conftest import seed_workspace
from fastapi import HTTPException

from api.config import settings
from api.models.agent import Agent
from api.models.user import User
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.query import Placement

DEFAULT_RUNTIME = "1.5"


class FakeWS:
    async def send_text(self, payload: str) -> None:
        json.loads(payload)


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


@pytest.fixture
def elastic(monkeypatch):
    monkeypatch.setattr(settings, "elastic_compute_enabled", True)


def _caps(**extra) -> dict:
    return {
        "duckdb_version": "1.5.5",
        "engine_version": "v1.5.5",
        "runtime_id": DEFAULT_RUNTIME,
        "extensions": ["httpfs", "iceberg"],
        **extra,
    }


@pytest.fixture
async def workspace(db_session):
    user = User(email="rc@test.local", password_hash=hash_password("pw"), name="R", role="user")
    db_session.add(user)
    await db_session.flush()
    ws, _ = await seed_workspace(db_session, user_id=user.id, slug="rc-ws", name="RC")
    ws.user = user
    return ws


async def _agent(db, name: str, *, connected: bool = False, **fields) -> Agent:
    fields.setdefault("status", "healthy")
    fields.setdefault("capabilities", _caps() if connected else None)
    agent = Agent(name=name, **fields)
    db.add(agent)
    await db.flush()
    if connected:
        registry.register(agent.id, FakeWS())  # type: ignore[arg-type]
    return agent


async def _decide(db, ws, **kwargs):
    return await query_service.resolve_compute(db, ws, principal_id=ws.user.id, **kwargs)


async def _make_default(db, ws, agent: Agent) -> None:
    ws.default_agent_id = agent.id
    await db.commit()


# --- no default: the old behaviour -------------------------------------------


async def test_without_a_default_a_connected_agent_is_used(db_session, workspace):
    agent = await _agent(db_session, "a", connected=True)
    await db_session.commit()

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.agent.id, decision.via_default) == (
        Placement.USE,
        agent.id,
        False,
    )


async def test_without_a_default_and_with_elastic_the_pool_scales_out(
    db_session, workspace, elastic
):
    decision = await _decide(db_session, workspace)
    assert decision.placement is Placement.SCALE_OUT


async def test_without_a_default_scale_out_can_be_refused(db_session, workspace, elastic):
    decision = await _decide(db_session, workspace, allow_scale_out=False)
    assert (decision.placement, decision.reason) == (Placement.UNAVAILABLE, "no_compute")
    assert not decision.via_default


async def test_without_a_default_and_without_elastic_nothing_is_unavailable(db_session, workspace):
    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.reason) == (Placement.UNAVAILABLE, "no_compute")


# --- a default: strict ---------------------------------------------------------


async def test_a_connected_default_is_used_even_when_another_agent_ranks_higher(
    db_session, workspace
):
    await _agent(db_session, "other", connected=True)
    default = await _agent(db_session, "default", connected=True)
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.agent.id, decision.via_default) == (
        Placement.USE,
        default.id,
        True,
    )


async def test_a_restricted_default_is_used_without_a_grant(db_session, workspace):
    default = await _agent(db_session, "locked", connected=True, access_mode="restricted")
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.agent.id) == (Placement.USE, default.id)


async def test_a_stopped_elastic_default_is_restarted(db_session, workspace, elastic):
    default = await _agent(db_session, "idle", provider="docker", lifecycle="terminated")
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.agent.id, decision.via_default) == (
        Placement.RESTART,
        default.id,
        True,
    )


async def test_a_failed_elastic_default_is_restarted(db_session, workspace, elastic):
    default = await _agent(db_session, "broken", provider="docker", lifecycle="failed")
    await _make_default(db_session, workspace, default)

    assert (await _decide(db_session, workspace)).placement is Placement.RESTART


async def test_a_starting_default_is_waited_for_not_restarted(db_session, workspace, elastic):
    default = await _agent(db_session, "booting", provider="docker", lifecycle="provisioning")
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.agent.id) == (Placement.WAIT, default.id)


async def test_the_default_is_never_replaced_by_the_pool_or_another_agent(
    db_session, workspace, elastic
):
    """A stopped default with a healthy agent available still restarts the default."""
    await _agent(db_session, "other", connected=True)
    default = await _agent(db_session, "idle", provider="docker", lifecycle="terminated")
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.agent.id) == (Placement.RESTART, default.id)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"provider": None}, "disconnected"),  # a static agent cannot be started
        ({"provider": "docker", "lifecycle": "running"}, "disconnected"),  # socket lost
        ({"provider": "docker", "lifecycle": "terminating"}, "stopping"),
    ],
)
async def test_a_default_that_cannot_be_started_is_unavailable(
    db_session, workspace, elastic, fields, reason
):
    default = await _agent(db_session, "gone", **fields)
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.reason, decision.via_default) == (
        Placement.UNAVAILABLE,
        reason,
        True,
    )
    assert decision.agent.id == default.id


async def test_an_elastic_default_is_unavailable_when_elastic_compute_is_off(db_session, workspace):
    default = await _agent(db_session, "idle", provider="docker", lifecycle="terminated")
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.reason) == (Placement.UNAVAILABLE, "elastic_disabled")


async def test_a_default_on_a_retired_runtime_cannot_be_restarted(db_session, workspace, elastic):
    default = await _agent(
        db_session,
        "old",
        provider="docker",
        lifecycle="terminated",
        requested_runtime_id="0.0-retired",
    )
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.reason) == (Placement.UNAVAILABLE, "runtime_retired")


async def test_a_connected_default_that_cannot_serve_the_workspace_is_incompatible(
    db_session, workspace
):
    default = await _agent(
        db_session, "no-iceberg", connected=True, capabilities=_caps(extensions=[])
    )
    await _make_default(db_session, workspace, default)

    decision = await _decide(db_session, workspace)
    assert (decision.placement, decision.reason) == (Placement.UNAVAILABLE, "incompatible")
    assert decision.refusal is not None


async def test_background_callers_fail_rather_than_wake_the_default(db_session, workspace, elastic):
    stopped = await _agent(db_session, "idle", provider="docker", lifecycle="terminated")
    await _make_default(db_session, workspace, stopped)
    assert (await _decide(db_session, workspace, allow_start=False)).reason == "stopped"

    stopped.lifecycle = "provisioning"
    await db_session.commit()
    assert (await _decide(db_session, workspace, allow_start=False)).reason == "starting"


async def test_housekeeping_ignores_the_default(db_session, workspace):
    """The maintenance scanner has no principal to embed a grant for, so it keeps
    choosing the way it always did."""
    other = await _agent(db_session, "other", connected=True)
    default = await _agent(db_session, "default", provider="docker", lifecycle="terminated")
    await _make_default(db_session, workspace, default)

    picked = await query_service.pick_agent_for(db_session, workspace, honor_default=False)
    assert picked is not None and picked.id == other.id


async def test_pick_agent_for_returns_only_a_default_that_is_up(db_session, workspace):
    default = await _agent(db_session, "default", connected=True)
    await _agent(db_session, "other", connected=True)
    await _make_default(db_session, workspace, default)
    picked = await query_service.pick_agent_for(
        db_session, workspace, principal_id=workspace.user.id
    )
    assert picked is not None and picked.id == default.id

    registry.unregister(default.id)
    assert (
        await query_service.pick_agent_for(db_session, workspace, principal_id=workspace.user.id)
        is None
    )


# --- how an unavailable decision is reported -----------------------------------


async def test_a_stopped_default_is_a_retryable_503_that_names_it(db_session, workspace, elastic):
    default = await _agent(db_session, "idle", provider="docker", lifecycle="terminated")
    await _make_default(db_session, workspace, default)
    decision = await _decide(db_session, workspace, allow_start=False)

    error = query_service.compute_unavailable_error(decision)
    assert error.status_code == 503
    assert error.headers == {"Retry-After": "5"}
    assert error.detail["error"] == "default_compute_unavailable"
    assert error.detail["agent_name"] == "idle"
    assert error.detail["reason"] == "stopped"
    assert "Default compute 'idle'" in error.detail["detail"]


async def test_an_incompatible_default_is_a_422_with_the_underlying_cause(db_session, workspace):
    default = await _agent(
        db_session, "no-iceberg", connected=True, capabilities=_caps(extensions=[])
    )
    await _make_default(db_session, workspace, default)
    decision = await _decide(db_session, workspace)

    error = query_service.compute_unavailable_error(decision)
    assert error.status_code == 422
    assert error.headers is None
    assert error.detail["cause"] == decision.refusal.code


async def test_no_default_keeps_the_old_codes(db_session, workspace, monkeypatch):
    decision = await _decide(db_session, workspace)
    assert query_service.compute_unavailable_error(decision).detail["error"] == "agent_required"

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    error = query_service.compute_unavailable_error(decision)
    assert error.status_code == 503
    assert error.detail["error"] == "no_compute_available"


async def test_no_agent_error_names_the_default_only_when_it_is_why(db_session, workspace):
    plain = await query_service.no_agent_error(
        db_session, workspace, principal_id=workspace.user.id, fallback="nothing is up"
    )
    assert plain.detail == "nothing is up"

    default = await _agent(db_session, "gone")
    await _make_default(db_session, workspace, default)
    named = await query_service.no_agent_error(
        db_session, workspace, principal_id=workspace.user.id, fallback="nothing is up"
    )
    assert isinstance(named, HTTPException)
    assert named.detail["error"] == "default_compute_unavailable"
