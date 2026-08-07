import uuid

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
    assert resp.status_code == 200
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
    assert resp.status_code == 200
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
    assert resp.json()["detail"]["error"] == "elastic_disabled"


async def test_create_elastic_agent_out_of_range_returns_422(
    admin_client: AsyncClient, elastic_enabled
):
    resp = await admin_client.post("/admin/agents/elastic", json={"cpu": 8, "memory_gb": 4})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_size"


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
    assert resp.json()["detail"]["error"] == "not_restartable"


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
    assert resp.json()["detail"]["error"] == "not_terminable"


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


async def test_monitoring_returns_every_series_on_one_grid(admin_client: AsyncClient, db_session):
    agent = Agent(name="mon-agent", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    resp = await admin_client.get(f"/admin/agents/{agent.id}/monitoring?window=1h")
    assert resp.status_code == 200
    data = resp.json()
    assert data["window"] == "1h"
    assert data["bucket_seconds"] == 60
    # The shared grid is the point: charts stacked vertically must line up.
    lengths = {
        len(data["peak_query_count"]),
        len(data["completed_query_count"]),
        len(data["activity"]),
        len(data["utilization"]),
    }
    assert lengths == {60}
    assert data["summary"]["completed"] == 0


async def test_monitoring_defaults_to_eight_hours(admin_client: AsyncClient, db_session):
    agent = Agent(name="mon-default", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    resp = await admin_client.get(f"/admin/agents/{agent.id}/monitoring")
    assert resp.status_code == 200
    assert resp.json()["window"] == "8h"


async def test_monitoring_rejects_an_unknown_window(admin_client: AsyncClient, db_session):
    agent = Agent(name="mon-bad-window", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    resp = await admin_client.get(f"/admin/agents/{agent.id}/monitoring?window=7d")
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
