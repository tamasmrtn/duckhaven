import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.user import Credential, User
from api.services.auth import hash_password


@pytest.fixture
async def admin(db_session):
    u = User(
        email="admin@agents.local", password_hash=hash_password("pw"), name="Admin", role="admin"
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, admin: User):
    await client.post("/auth/login", json={"email": "admin@agents.local", "password": "pw"})
    return client


async def test_bootstrap_creates_token(admin_client: AsyncClient):
    resp = await admin_client.post("/admin/agents/bootstrap")
    assert resp.status_code == 201
    data = resp.json()
    assert data["token"].startswith("dh_boot_")
    assert "expires_at" in data
    assert data["control_plane_url"].endswith("/agents/connect")
    assert data["agent_image"].startswith("ghcr.io/")


async def test_bootstrap_derives_wss_from_forwarded_proto(admin_client: AsyncClient):
    resp = await admin_client.post(
        "/admin/agents/bootstrap",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "duckhaven.example.com"},
    )
    assert resp.status_code == 201
    assert resp.json()["control_plane_url"] == "wss://duckhaven.example.com/agents/connect"


async def test_list_agents_empty(admin_client: AsyncClient):
    resp = await admin_client.get("/admin/agents")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_metrics_empty(admin_client: AsyncClient):
    resp = await admin_client.get("/admin/agents/metrics")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_metrics_returns_samples_with_name(admin_client: AsyncClient, db_session):
    agent = Agent(name="busy-agent", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    from api.services.agent_registry import registry

    registry.register(agent.id, object())  # type: ignore[arg-type]
    registry.record_metrics(
        agent.id,
        {
            "cpu_percent": 33.0,
            "memory_percent": 50.0,
            "running_queries": 2,
            "queued_queries": 3,
            "active_profile": "decaying_3",
            "sampled_at": "2026-06-05T00:00:00Z",
        },
    )
    try:
        resp = await admin_client.get("/admin/agents/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "busy-agent"
        assert data[0]["agent_id"] == str(agent.id)
        sample = data[0]["samples"][0]
        assert sample["cpu_percent"] == 33.0
        assert sample["memory_percent"] == 50.0
        # Admission counts + active profile round-trip to the Utilization page.
        assert sample["running_queries"] == 2
        assert sample["queued_queries"] == 3
        assert sample["active_profile"] == "decaying_3"
    finally:
        registry.unregister(agent.id)


async def test_revoke_nonexistent_agent(admin_client: AsyncClient):
    resp = await admin_client.delete(f"/admin/agents/{uuid.uuid4()}/credential")
    assert resp.status_code == 404


async def test_revoke_agent_marks_unavailable(admin_client: AsyncClient, db_session, admin: User):
    agent = Agent(name="test-agent", status="healthy")
    db_session.add(agent)
    await db_session.flush()
    cred = Credential(
        user_id=None,
        agent_id=agent.id,
        kind="agent_session",
        token="tok-revoke-test",
        expires_at=None,
    )
    db_session.add(cred)
    await db_session.commit()

    resp = await admin_client.delete(f"/admin/agents/{agent.id}/credential")
    assert resp.status_code == 204

    await db_session.refresh(agent)
    assert agent.status == "unavailable"


# --- elastic compute (create sized ACI agents) ---


@pytest.fixture
def elastic_enabled(monkeypatch):
    from api.config import settings
    from api.services.compute.backends import get_backend

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(settings, "elastic_provider", "null")
    backend = get_backend("null")
    backend._instances.clear()
    yield
    backend._instances.clear()


async def test_compute_options_returns_ranges_and_rates(admin_client: AsyncClient):
    resp = await admin_client.get("/admin/agents/compute-options")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cpu_min"] == 1 and body["cpu_max"] == 4
    assert body["memory_min_gb"] == 1 and body["memory_max_gb"] == 16
    assert body["price_vcpu_hour"] == 0.0486
    assert body["price_memory_gb_hour"] == 0.0054
    assert body["default_idle_minutes"] == 15  # 900s default


async def test_create_elastic_agent_disabled_returns_409(admin_client: AsyncClient):
    resp = await admin_client.post("/admin/agents/elastic", json={"cpu": 1, "memory_gb": 4})
    assert resp.status_code == 409
    assert resp.json()["error"] == "elastic_disabled"


async def test_create_elastic_agent_out_of_range_returns_422(
    admin_client: AsyncClient, elastic_enabled
):
    resp = await admin_client.post("/admin/agents/elastic", json={"cpu": 8, "memory_gb": 4})
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_size"


async def test_create_elastic_agent_provisions_with_size_cost_and_idle(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 4, "memory_gb": 16, "name": "warehouse", "idle_timeout_minutes": 10},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["provider"] == "null"
    assert body["lifecycle"] == "provisioning"
    assert body["requested_cpu"] == 4 and body["requested_memory_gb"] == 16
    # 4 * 0.0486 + 16 * 0.0054 = 0.2808
    assert body["hourly_cost"] == 0.2808
    assert body["idle_timeout_minutes"] == 10

    from sqlalchemy import select

    agent = (await db_session.execute(select(Agent).where(Agent.name == "warehouse"))).scalar_one()
    assert agent.lifecycle == "provisioning"
    assert agent.requested_cpu == 4
    assert agent.idle_timeout_s == 600


async def test_create_elastic_agent_accepts_a_max_timeout_s(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    """A long analytical job needs a raised ceiling above the agent image's 600s
    default; the request must reach the row so a restart can reuse it."""
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 4, "memory_gb": 16, "name": "long-job", "max_timeout_s": 14400},
    )
    assert resp.status_code == 202
    assert resp.json()["requested_max_timeout_s"] == 14400

    from sqlalchemy import select

    agent = (await db_session.execute(select(Agent).where(Agent.name == "long-job"))).scalar_one()
    assert agent.requested_max_timeout_s == 14400


async def test_create_elastic_agent_omits_max_timeout_s_by_default(
    admin_client: AsyncClient, elastic_enabled
):
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 1, "memory_gb": 2, "name": "default-timeout"},
    )
    assert resp.status_code == 202
    assert resp.json()["requested_max_timeout_s"] is None


async def test_create_elastic_agent_rejects_a_nonpositive_max_timeout_s(
    admin_client: AsyncClient, elastic_enabled
):
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 1, "memory_gb": 2, "max_timeout_s": 0},
    )
    assert resp.status_code == 422


async def test_create_elastic_agent_rejects_an_excessive_max_timeout_s(
    admin_client: AsyncClient, elastic_enabled
):
    """Bounded so a fat-fingered value can't ask for an effectively unbounded
    runaway query rather than a genuine large analytical job."""
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 1, "memory_gb": 2, "max_timeout_s": 100000},
    )
    assert resp.status_code == 422


async def test_restart_terminated_elastic_agent(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    from datetime import UTC, datetime

    agent = Agent(
        name="warehouse",
        status="unavailable",
        provider="null",
        lifecycle="terminated",
        requested_cpu=2,
        requested_memory_gb=8,
        idle_timeout_s=600,
        provisioned_at=datetime.now(tz=UTC),
        terminated_at=datetime.now(tz=UTC),
    )
    db_session.add(agent)
    await db_session.commit()

    resp = await admin_client.post(f"/admin/agents/{agent.id}/restart")
    assert resp.status_code == 202
    assert resp.json()["lifecycle"] == "provisioning"
    await db_session.refresh(agent)
    assert agent.lifecycle == "provisioning"
    assert agent.terminated_at is None
    assert agent.requested_cpu == 2  # size preserved


async def test_restart_preserves_the_requested_max_timeout_s(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    from datetime import UTC, datetime

    agent = Agent(
        name="long-job",
        status="unavailable",
        provider="null",
        lifecycle="terminated",
        requested_cpu=2,
        requested_memory_gb=8,
        requested_max_timeout_s=14400,
        idle_timeout_s=600,
        provisioned_at=datetime.now(tz=UTC),
        terminated_at=datetime.now(tz=UTC),
    )
    db_session.add(agent)
    await db_session.commit()

    resp = await admin_client.post(f"/admin/agents/{agent.id}/restart")
    assert resp.status_code == 202
    assert resp.json()["requested_max_timeout_s"] == 14400
    await db_session.refresh(agent)
    assert agent.requested_max_timeout_s == 14400


async def test_restart_running_agent_rejected(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    from datetime import UTC, datetime

    agent = Agent(
        name="live",
        status="healthy",
        provider="null",
        lifecycle="running",
        requested_cpu=1,
        requested_memory_gb=4,
        provisioned_at=datetime.now(tz=UTC),
    )
    db_session.add(agent)
    await db_session.commit()
    resp = await admin_client.post(f"/admin/agents/{agent.id}/restart")
    assert resp.status_code == 409
    assert resp.json()["error"] == "not_restartable"


async def test_terminate_running_agent(admin_client: AsyncClient, db_session, elastic_enabled):
    from datetime import UTC, datetime

    from api.services.compute.backends import get_backend

    agent = Agent(
        name="live",
        status="healthy",
        provider="null",
        lifecycle="running",
        instance_id="dh-live",
        requested_cpu=1,
        requested_memory_gb=4,
        provisioned_at=datetime.now(tz=UTC),
    )
    db_session.add(agent)
    await db_session.commit()
    get_backend("null")._instances.add("dh-live")

    resp = await admin_client.post(f"/admin/agents/{agent.id}/terminate")
    assert resp.status_code == 202
    assert resp.json()["lifecycle"] == "terminated"
    assert "dh-live" not in get_backend("null")._instances
    await db_session.refresh(agent)
    assert agent.lifecycle == "terminated"


async def test_terminate_terminated_agent_rejected(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    from datetime import UTC, datetime

    agent = Agent(
        name="gone",
        status="unavailable",
        provider="null",
        lifecycle="terminated",
        provisioned_at=datetime.now(tz=UTC),
    )
    db_session.add(agent)
    await db_session.commit()
    resp = await admin_client.post(f"/admin/agents/{agent.id}/terminate")
    assert resp.status_code == 409
    assert resp.json()["error"] == "not_terminable"


async def test_delete_agent_removes_row_and_nulls_query_link(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    """Deleting an agent with query history keeps the query but nulls its agent."""
    import uuid as _uuid
    from datetime import UTC, datetime

    from conftest import seed_workspace

    from api.models.query import Query
    from api.services.compute.backends import get_backend

    ws, _ = await seed_workspace(db_session, user_id=_uuid.uuid4())
    agent = Agent(
        name="doomed",
        status="healthy",
        provider="null",
        lifecycle="running",
        instance_id="dh-doomed",
        provisioned_at=datetime.now(tz=UTC),
    )
    db_session.add(agent)
    await db_session.flush()
    q = Query(workspace_id=ws.id, agent_id=agent.id, sql="SELECT 1", status="done")
    db_session.add(q)
    await db_session.commit()
    get_backend("null")._instances.add("dh-doomed")

    resp = await admin_client.delete(f"/admin/agents/{agent.id}")
    assert resp.status_code == 204

    from sqlalchemy import select

    gone = await db_session.execute(select(Agent).where(Agent.id == agent.id))
    assert gone.scalar_one_or_none() is None
    # The running instance was destroyed, and the query survives with a null agent.
    assert "dh-doomed" not in get_backend("null")._instances
    await db_session.refresh(q)
    assert q.agent_id is None


async def test_create_elastic_agent_rejects_a_nonpositive_idle_timeout(
    admin_client: AsyncClient, elastic_enabled
):
    """The value becomes seconds and is compared against the idle clock, so anything at
    or below zero makes the reaper terminate the agent on its first tick -- seconds after
    it was asked for. The dialog's min is presentation only."""
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 1, "memory_gb": 2, "idle_timeout_minutes": -5},
    )
    assert resp.status_code == 422


async def test_create_elastic_agent_accepts_a_sane_idle_timeout(
    admin_client: AsyncClient, elastic_enabled
):
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 1, "memory_gb": 2, "idle_timeout_minutes": 30},
    )
    assert resp.status_code == 202
    assert resp.json()["idle_timeout_minutes"] == 30


# ── Detail + monitoring ──────────────────────────────────────────────────────


async def test_get_agent_returns_one_agent(admin_client: AsyncClient, db_session):
    agent = Agent(name="detail-agent", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    resp = await admin_client.get(f"/admin/agents/{agent.id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "detail-agent"


async def test_get_agent_404s_for_an_unknown_id(admin_client: AsyncClient):
    resp = await admin_client.get(f"/admin/agents/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_literal_paths_are_not_shadowed_by_the_id_route(admin_client: AsyncClient):
    """GET /{agent_id} is declared after /metrics and /compute-options; if it ever
    moves above them, FastAPI matches first and tries to parse "metrics" as a UUID."""
    assert (await admin_client.get("/admin/agents/metrics")).status_code == 200
    assert (await admin_client.get("/admin/agents/compute-options")).status_code == 200


async def _mon_agent(db_session, name: str) -> Agent:
    agent = Agent(name=name, status="healthy")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    return agent


async def test_monitoring_returns_every_series_on_one_grid(admin_client: AsyncClient, db_session):
    agent = await _mon_agent(db_session, "mon-agent")

    resp = await admin_client.get(f"/admin/agents/{agent.id}/monitoring?window=1h")
    assert resp.status_code == 200
    data = resp.json()
    assert data["preset"] == "1h"
    assert data["bucket_seconds"] == 60
    # One flat row per bucket is what keeps stacked charts aligned.
    assert 60 <= len(data["buckets"]) <= 61
    assert {"busy_s", "running_avg", "cpu_avg", "done"} <= set(data["buckets"][0])
    assert data["summary"]["finished"] == 0


async def test_monitoring_defaults_to_eight_hours(admin_client: AsyncClient, db_session):
    agent = await _mon_agent(db_session, "mon-default")
    resp = await admin_client.get(f"/admin/agents/{agent.id}/monitoring")
    assert resp.status_code == 200
    assert resp.json()["preset"] == "8h"


async def test_monitoring_offers_seven_days_at_two_hour_buckets(
    admin_client: AsyncClient, db_session
):
    """Retention is a week, so the page can show one."""
    agent = await _mon_agent(db_session, "mon-7d")
    resp = await admin_client.get(f"/admin/agents/{agent.id}/monitoring?window=7d")
    assert resp.status_code == 200
    assert resp.json()["bucket_seconds"] == 7200


async def test_monitoring_accepts_a_zoomed_range(admin_client: AsyncClient, db_session):
    agent = await _mon_agent(db_session, "mon-zoom")
    end = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(hours=1)
    start = end - timedelta(minutes=30)
    resp = await admin_client.get(
        f"/admin/agents/{agent.id}/monitoring",
        params={"start": start.isoformat(), "end": end.isoformat()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["preset"] is None
    assert data["bucket_seconds"] == 60


@pytest.mark.parametrize(
    "params",
    [
        {"window": "2w"},
        {"window": "1h", "start": "2026-09-30T10:00:00Z", "end": "2026-09-30T11:00:00Z"},
        {"start": "2026-09-30T10:00:00Z"},
        {"start": "2026-09-30T11:00:00Z", "end": "2026-09-30T10:00:00Z"},
        {"start": "2026-09-30T10:00:00Z", "end": "2026-09-30T10:02:00Z"},
    ],
    ids=["unknown-window", "window-and-range", "start-only", "backwards", "too-narrow"],
)
async def test_monitoring_rejects_an_unusable_range(admin_client: AsyncClient, db_session, params):
    agent = await _mon_agent(db_session, "mon-bad-range")
    resp = await admin_client.get(f"/admin/agents/{agent.id}/monitoring", params=params)
    assert resp.status_code == 422


async def test_monitoring_404s_for_an_unknown_agent(admin_client: AsyncClient):
    resp = await admin_client.get(f"/admin/agents/{uuid.uuid4()}/monitoring")
    assert resp.status_code == 404


async def test_detail_and_monitoring_hidden_on_a_restricted_agent(client: AsyncClient, db_session):
    """A restricted agent is invisible to an ungranted caller — 404, not 403.

    Replaces the old "requires agents:manage" assertion. Detail and monitoring are
    now `use`-tier surfaces, so what gates them is the agent's access mode plus the
    caller's grants, not the global permission.
    """
    from api.services.auth import hash_password

    member = User(
        email="member@agents.local", password_hash=hash_password("pw"), name="M", role="user"
    )
    agent = Agent(name="guarded", status="healthy", access_mode="restricted")
    db_session.add_all([member, agent])
    await db_session.commit()
    await db_session.refresh(agent)
    await client.post("/auth/login", json={"email": "member@agents.local", "password": "pw"})

    assert (await client.get(f"/admin/agents/{agent.id}")).status_code == 404
    assert (await client.get(f"/admin/agents/{agent.id}/monitoring")).status_code == 404
    # ... and it is absent from the listing rather than 403-ing it.
    listed = await client.get("/admin/agents")
    assert listed.status_code == 200
    assert [a for a in listed.json() if a["id"] == str(agent.id)] == []


async def test_detail_and_monitoring_open_to_any_caller_on_an_open_agent(
    client: AsyncClient, db_session
):
    """An `open` agent floors every authenticated caller at `use`, which includes
    reading its status and monitoring page — but no lifecycle action."""
    from api.services.auth import hash_password

    member = User(
        email="member2@agents.local", password_hash=hash_password("pw"), name="M2", role="user"
    )
    agent = Agent(name="shared", status="healthy")
    db_session.add_all([member, agent])
    await db_session.commit()
    await db_session.refresh(agent)
    await client.post("/auth/login", json={"email": "member2@agents.local", "password": "pw"})

    detail = await client.get(f"/admin/agents/{agent.id}")
    assert detail.status_code == 200
    assert detail.json()["access_tier"] == "use"
    assert (await client.get(f"/admin/agents/{agent.id}/monitoring")).status_code == 200
    # `use` stops short of every lifecycle and administration action.
    assert (await client.post(f"/admin/agents/{agent.id}/disconnect")).status_code == 403
    assert (await client.delete(f"/admin/agents/{agent.id}")).status_code == 403
    assert (await client.get(f"/admin/agents/{agent.id}/access")).status_code == 403


# --- the tier x endpoint matrix ----------------------------------------------


@pytest.fixture
async def elastic_agent(db_session):
    """A terminated elastic agent: restartable, and restricted so only grants speak."""
    a = Agent(
        name="elastic-1",
        status="unavailable",
        access_mode="restricted",
        provider="null",
        lifecycle="terminated",
        instance_id="dh-agent-test",
        requested_cpu=2.0,
        requested_memory_gb=4.0,
    )
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest.fixture
async def grantee(db_session):
    u = User(email="grantee@agents.local", password_hash=hash_password("pw"), name="G", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def grantee_client(client: AsyncClient, grantee: User):
    await client.post("/auth/login", json={"email": "grantee@agents.local", "password": "pw"})
    return client


async def _grant(db_session, agent: Agent, user: User, tier: str) -> None:
    from api.models.agent_grant import AgentGrant

    db_session.add(AgentGrant(agent_id=agent.id, user_id=user.id, tier=tier))
    await db_session.commit()


@pytest.mark.parametrize(
    ("tier", "detail", "restart", "disconnect", "delete", "access"),
    [
        # tier      detail  restart  disconnect  delete  access
        ("use", 200, 403, 403, 403, 403),
        ("operate", 200, 202, 202, 403, 403),
        ("admin", 200, 202, 202, 204, 200),
    ],
)
async def test_each_tier_unlocks_exactly_its_endpoints(
    grantee_client: AsyncClient,
    db_session,
    elastic_agent: Agent,
    grantee: User,
    elastic_enabled,
    tier,
    detail,
    restart,
    disconnect,
    delete,
    access,
):
    await _grant(db_session, elastic_agent, grantee, tier)
    aid = elastic_agent.id

    assert (await grantee_client.get(f"/admin/agents/{aid}")).status_code == detail
    assert (await grantee_client.get(f"/admin/agents/{aid}/access")).status_code == access
    assert (await grantee_client.post(f"/admin/agents/{aid}/disconnect")).status_code == disconnect
    assert (await grantee_client.post(f"/admin/agents/{aid}/restart")).status_code == restart
    # Delete last: it removes the row every later call would need.
    assert (await grantee_client.delete(f"/admin/agents/{aid}")).status_code == delete


async def test_fleet_level_actions_stay_on_the_global_permission(
    grantee_client: AsyncClient, db_session, elastic_agent: Agent, grantee: User
):
    """Creating agents is a spend decision about the fleet, so Tier 3 on one agent
    never confers it."""
    await _grant(db_session, elastic_agent, grantee, "admin")
    assert (await grantee_client.post("/admin/agents/bootstrap")).status_code == 403
    assert (await grantee_client.get("/admin/agents/compute-options")).status_code == 403
    assert (
        await grantee_client.post("/admin/agents/elastic", json={"cpu": 2, "memory_gb": 4})
    ).status_code == 403


async def test_listing_annotates_each_row_with_the_callers_tier(
    grantee_client: AsyncClient, db_session, elastic_agent: Agent, grantee: User
):
    open_agent = Agent(name="shared-open", status="healthy")
    db_session.add(open_agent)
    await db_session.commit()
    await _grant(db_session, elastic_agent, grantee, "operate")

    rows = {a["name"]: a for a in (await grantee_client.get("/admin/agents")).json()}
    assert rows["elastic-1"]["access_tier"] == "operate"
    assert rows["elastic-1"]["access_mode"] == "restricted"
    assert rows["shared-open"]["access_tier"] == "use"


async def test_metrics_are_filtered_to_visible_agents(
    grantee_client: AsyncClient, elastic_agent: Agent
):
    """Telemetry is as sensitive as the monitoring page it feeds."""
    resp = await grantee_client.get("/admin/agents/metrics")
    assert resp.status_code == 200
    assert [m for m in resp.json() if m["agent_id"] == str(elastic_agent.id)] == []


async def test_create_elastic_agent_defaults_to_open(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    resp = await admin_client.post(
        "/admin/agents/elastic", json={"cpu": 2, "memory_gb": 4, "name": "shared-1"}
    )
    assert resp.status_code == 202
    assert resp.json()["access_mode"] == "open"


async def test_create_elastic_agent_can_start_restricted(
    admin_client: AsyncClient, db_session, elastic_enabled
):
    """Chosen at creation so a reserved agent is never briefly usable by everyone:
    it registers and starts taking work before anyone could open the Access tab."""
    from sqlalchemy import select

    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 2, "memory_gb": 4, "name": "reserved-1", "access_mode": "restricted"},
    )
    assert resp.status_code == 202
    assert resp.json()["access_mode"] == "restricted"
    # Persisted on the row, not just echoed back.
    agent = (await db_session.execute(select(Agent).where(Agent.name == "reserved-1"))).scalar_one()
    assert agent.access_mode == "restricted"


async def test_create_elastic_agent_rejects_unknown_access_mode(
    admin_client: AsyncClient, elastic_enabled
):
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 2, "memory_gb": 4, "access_mode": "public"},
    )
    assert resp.status_code == 422


async def test_a_restricted_new_agent_is_hidden_from_others(
    admin_client: AsyncClient, grantee: User, elastic_enabled
):
    """The point of setting it at creation: nobody else can see it, from the moment
    the row exists."""
    from httpx import ASGITransport

    from api.main import api_app

    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 2, "memory_gb": 4, "name": "reserved-2", "access_mode": "restricted"},
    )
    assert resp.status_code == 202
    agent_id = resp.json()["id"]

    # A second cookie jar: logging the grantee in on `admin_client` would replace
    # the admin's session, and the creation above needs to happen as the admin.
    async with AsyncClient(transport=ASGITransport(app=api_app), base_url="http://test") as other:
        await other.post("/auth/login", json={"email": "grantee@agents.local", "password": "pw"})
        listed = await other.get("/agents")
        assert [a for a in listed.json() if a["id"] == agent_id] == []
        assert (await other.get(f"/admin/agents/{agent_id}")).status_code == 404


# ── Runtimes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def more_runtimes(monkeypatch):
    """A beta and a deprecated runtime alongside the real default."""
    from dataclasses import replace

    from duckhaven_shared import runtimes

    base = runtimes.RUNTIMES["1.5"]
    for rid, status in (("2.0", "beta"), ("1.4", "deprecated"), ("1.3", "retired")):
        monkeypatch.setitem(
            runtimes.RUNTIMES,
            rid,
            replace(base, id=rid, display_name=f"DuckDB {rid}", duckdb_line=rid, status=status),
        )


@pytest.fixture
def provisioned_images(monkeypatch):
    from api.services.compute.backends import get_backend

    images: list[str] = []
    backend = get_backend("null")
    original = backend.provision

    async def recording(req):
        images.append(req.image)
        return await original(req)

    monkeypatch.setattr(backend, "provision", recording)
    return images


async def test_new_compute_runs_the_default_runtime_unless_told_otherwise(
    admin_client: AsyncClient, db_session, elastic_enabled, provisioned_images
):
    resp = await admin_client.post("/admin/agents/elastic", json={"cpu": 1, "memory_gb": 4})
    assert resp.status_code == 202
    body = resp.json()
    assert body["runtime"] == {
        "id": "1.5",
        "display_name": "DuckDB 1.5",
        "status": "ga",
        "state": "pending",
        "default": True,
    }
    assert provisioned_images[-1].endswith("-duckdb1.5")


async def test_a_beta_runtime_needs_an_explicit_opt_in(
    admin_client: AsyncClient, db_session, elastic_enabled, more_runtimes, provisioned_images
):
    refused = await admin_client.post(
        "/admin/agents/elastic", json={"cpu": 1, "memory_gb": 4, "runtime_id": "2.0"}
    )
    assert refused.status_code == 422
    assert refused.json()["error"] == "runtime_beta"

    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 1, "memory_gb": 4, "runtime_id": "2.0", "allow_beta": True},
    )
    assert resp.status_code == 202
    assert resp.json()["runtime"]["id"] == "2.0"
    assert provisioned_images[-1].endswith("-duckdb2.0")
    agent = await db_session.get(Agent, uuid.UUID(resp.json()["id"]))
    assert agent.requested_runtime_id == "2.0"


@pytest.mark.parametrize(
    ("runtime_id", "error"),
    [("1.4", "runtime_unavailable"), ("1.3", "runtime_unavailable"), ("9.9", "unknown_runtime")],
)
async def test_new_compute_cannot_use_a_closed_or_unknown_runtime(
    admin_client: AsyncClient, elastic_enabled, more_runtimes, runtime_id, error
):
    resp = await admin_client.post(
        "/admin/agents/elastic",
        json={"cpu": 1, "memory_gb": 4, "runtime_id": runtime_id, "allow_beta": True},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == error


async def test_compute_options_offer_only_runtimes_open_to_new_compute(
    admin_client: AsyncClient, more_runtimes
):
    body = (await admin_client.get("/admin/agents/compute-options")).json()
    assert body["default_runtime"] == "1.5"
    assert [r["id"] for r in body["runtimes"]] == ["1.5", "2.0"]
    default = body["runtimes"][0]
    assert default["default"] is True
    assert "ducklake" in default["extensions"]


async def test_the_add_agent_snippet_follows_the_chosen_runtime(
    admin_client: AsyncClient, more_runtimes
):
    default = (await admin_client.post("/admin/agents/bootstrap")).json()
    assert default["runtime_id"] == "1.5"
    assert default["agent_image"].endswith("-duckdb1.5")

    beta = (await admin_client.post("/admin/agents/bootstrap", json={"runtime_id": "2.0"})).json()
    assert beta["runtime_id"] == "2.0"
    assert beta["agent_image"].endswith("-duckdb2.0")


async def test_restart_keeps_the_runtime_and_refuses_a_retired_one(
    admin_client: AsyncClient, db_session, elastic_enabled, more_runtimes, provisioned_images
):
    from datetime import UTC, datetime

    def terminated(runtime_id: str) -> Agent:
        return Agent(
            name=f"rt-{runtime_id}",
            status="unavailable",
            provider="null",
            lifecycle="terminated",
            requested_cpu=1,
            requested_memory_gb=4,
            requested_runtime_id=runtime_id,
            capabilities={"duckdb_version": "1.5.4", "extensions": []},
            terminated_at=datetime.now(tz=UTC),
        )

    kept, retired = terminated("2.0"), terminated("1.3")
    db_session.add_all([kept, retired])
    await db_session.commit()

    resp = await admin_client.post(f"/admin/agents/{kept.id}/restart")
    assert resp.status_code == 202
    assert provisioned_images[-1].endswith("-duckdb2.0")
    await db_session.refresh(kept)
    # The previous instance's report says nothing about the next one.
    assert kept.capabilities is None

    refused = await admin_client.post(f"/admin/agents/{retired.id}/restart")
    assert refused.status_code == 409
    assert refused.json()["error"] == "runtime_retired"


async def test_runtimes_are_listed_for_any_signed_in_user(admin_client: AsyncClient, more_runtimes):
    resp = await admin_client.get("/runtimes")
    assert resp.status_code == 200
    by_id = {r["id"]: r for r in resp.json()}
    # Retired ones included, so an agent still running one can be labelled.
    assert set(by_id) == {"1.5", "2.0", "1.4", "1.3"}
    assert by_id["1.5"]["default"] is True
    assert by_id["2.0"]["status"] == "beta"


# ── Status from presence, not from the stored column ─────────────────────────


async def test_a_gone_agent_whose_row_still_says_healthy_shows_unavailable(
    admin_client: AsyncClient, db_session
):
    """When the API holding an agent's socket stops before recording the
    disconnect, the row keeps saying `healthy` and nothing writes it back. The
    list and the detail page used to show that stale value."""
    agent = Agent(name="gone", status="healthy")
    db_session.add(agent)
    await db_session.commit()

    listed = {a["id"]: a for a in (await admin_client.get("/admin/agents")).json()}
    assert listed[str(agent.id)]["status"] == "unavailable"
    detail = (await admin_client.get(f"/admin/agents/{agent.id}")).json()
    assert detail["status"] == "unavailable"
    picker = {a["id"]: a for a in (await admin_client.get("/agents")).json()}
    assert picker[str(agent.id)]["status"] == "unavailable"


async def test_a_connected_agent_shows_healthy_before_its_row_catches_up(
    admin_client: AsyncClient, db_session
):
    from api.services.agent_registry import registry

    agent = Agent(name="just-connected", status="unavailable")
    db_session.add(agent)
    await db_session.commit()
    registry.register(agent.id, object())  # type: ignore[arg-type]
    try:
        detail = (await admin_client.get(f"/admin/agents/{agent.id}")).json()
        assert detail["status"] == "healthy"
        listed = {a["id"]: a for a in (await admin_client.get("/admin/agents")).json()}
        assert listed[str(agent.id)]["status"] == "healthy"
    finally:
        registry.unregister(agent.id)


# ── Per-agent query list ─────────────────────────────────────────────────────


@pytest.fixture
async def ranked_agent(db_session, admin: User):
    """An agent with runs of known cost inside the last hour, plus decoys."""
    from conftest import seed_workspace

    from api.models.query import Query as QueryRow

    ws, _ = await seed_workspace(db_session, user_id=admin.id)
    agent = Agent(name="ranked", status="healthy")
    other = Agent(name="elsewhere", status="healthy")
    db_session.add_all([agent, other])
    await db_session.commit()
    now = datetime.now(tz=UTC)

    def run(target, *, sql, started_min, ran_min=None, finished_min=None, **kw):
        profile = kw.pop("profile", None)
        return QueryRow(
            workspace_id=ws.id,
            agent_id=None if kw.pop("parked", False) else target.id,
            requested_agent_id=target.id,
            sql=sql,
            status=kw.pop("status", "done"),
            started_at=now - timedelta(minutes=started_min),
            running_at=None if ran_min is None else now - timedelta(minutes=ran_min),
            finished_at=None if finished_min is None else now - timedelta(minutes=finished_min),
            profile={"summary": profile} if profile else None,
            **kw,
        )

    db_session.add_all(
        [
            run(
                agent,
                sql="big",
                started_min=30,
                ran_min=29,
                finished_min=28,
                profile={"peak_memory_bytes": 5_000_000_000, "cpu_time_ms": 900.0},
            ),
            run(
                agent,
                sql="small",
                started_min=20,
                ran_min=20,
                finished_min=19,
                profile={"peak_memory_bytes": 1_000, "cpu_time_ms": 5.0},
            ),
            run(agent, sql="no profile", started_min=15, ran_min=15, finished_min=14),
            # Started before the range, finished inside it: still part of it.
            run(
                agent,
                sql="spans the start",
                started_min=90,
                ran_min=90,
                finished_min=50,
                profile={"peak_memory_bytes": 2_000},
            ),
            run(
                agent,
                sql="failed typo",
                started_min=10,
                finished_min=10,
                status="failed",
                error="Parser Error: syntax error",
            ),
            run(
                agent,
                sql="parked",
                started_min=5,
                finished_min=4,
                status="failed",
                error="No compute became available",
                parked=True,
            ),
            # Decoys.
            run(agent, sql="finished before", started_min=200, ran_min=200, finished_min=180),
            run(
                agent,
                sql="metadata probe",
                started_min=10,
                ran_min=10,
                finished_min=9,
                origin="metadata",
            ),
            run(other, sql="other agent", started_min=10, ran_min=10, finished_min=9),
        ]
    )
    await db_session.commit()
    start = (now - timedelta(hours=1)).isoformat()
    return agent, {"start": start, "end": now.isoformat()}


async def test_agent_queries_list_the_runs_alive_in_the_range(admin_client, ranked_agent):
    agent, rng = ranked_agent
    resp = await admin_client.get(f"/admin/agents/{agent.id}/queries", params=rng)
    assert resp.status_code == 200
    body = resp.json()
    assert [q["sql"] for q in body["items"]] == [
        "parked",
        "failed typo",
        "no profile",
        "small",
        "big",
        "spans the start",
    ]
    by_sql = {q["sql"]: q for q in body["items"]}
    assert by_sql["big"]["peak_memory_bytes"] == 5_000_000_000
    assert by_sql["big"]["wait_ms"] == 60_000
    assert by_sql["failed typo"]["failure_reason"] == "sql_error"
    assert by_sql["failed typo"]["error"] == "Parser Error: syntax error"
    assert by_sql["parked"]["failure_reason"] == "no_compute"
    assert by_sql["no profile"]["peak_memory_bytes"] is None


@pytest.mark.parametrize("direction", ["desc", "asc"])
async def test_agent_queries_sort_by_peak_memory_with_unknowns_last(
    admin_client, ranked_agent, direction
):
    agent, rng = ranked_agent
    resp = await admin_client.get(
        f"/admin/agents/{agent.id}/queries",
        params={**rng, "sort": "peak_memory", "dir": direction},
    )
    memories = [q["peak_memory_bytes"] for q in resp.json()["items"]]
    known = [m for m in memories if m is not None]
    assert known == sorted(known, reverse=direction == "desc")
    assert memories[: len(known)] == known, "a run with no profile headed the list"


async def test_agent_queries_page_without_repeating_or_skipping(admin_client, ranked_agent):
    agent, rng = ranked_agent
    seen, cursor = [], None
    for _ in range(10):
        params = {**rng, "sort": "cpu_time", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = (await admin_client.get(f"/admin/agents/{agent.id}/queries", params=params)).json()
        seen += [q["id"] for q in body["items"]]
        cursor = body["cursor"]
        if not body["has_more"]:
            break
    assert len(seen) == len(set(seen)) == 6


async def test_agent_queries_reject_a_bad_cursor(admin_client, ranked_agent):
    agent, rng = ranked_agent
    resp = await admin_client.get(
        f"/admin/agents/{agent.id}/queries",
        params={**rng, "sort": "wait", "cursor": "not-a-cursor"},
    )
    assert resp.status_code == 422


async def test_agent_queries_need_cross_workspace_query_access(client, db_session):
    """Other workspaces' SQL: the agent tier alone is not enough."""
    from api.services.auth import hash_password

    db_session.add_all(
        [
            User(email="m3@agents.local", password_hash=hash_password("pw"), name="M", role="user"),
            agent := Agent(name="open-shared", status="healthy"),
        ]
    )
    await db_session.commit()
    await client.post("/auth/login", json={"email": "m3@agents.local", "password": "pw"})
    now = datetime.now(tz=UTC)
    resp = await client.get(
        f"/admin/agents/{agent.id}/queries",
        params={"start": (now - timedelta(hours=1)).isoformat(), "end": now.isoformat()},
    )
    assert resp.status_code == 403
