"""What the publisher reads from the control plane: which runs, and which grants.

The copy itself (the transaction horizon, the cursors, the sweep, retention) runs
against Postgres in tests/integration/test_system_catalog_publisher.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.catalog_grant import CatalogGrant
from api.models.query import Query
from api.models.user import SERVICE_ACCOUNT_PROVIDER, User
from api.models.workspace import Workspace
from api.services.system_catalog import store
from api.services.system_catalog.publisher import grant_rows, history_rows

from ...conftest import seed_workspace

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)


async def _people(db) -> tuple[User, User]:
    ana = User(email="ana@example.com", name="Ana", role="admin")
    robot = User(
        email="robot@service-account.local", name="Robot", auth_provider=SERVICE_ACCOUNT_PROVIDER
    )
    db.add_all([ana, robot])
    await db.commit()
    return ana, robot


async def test_history_reads_finished_runs_and_not_the_machinery(db_session):
    ana, _ = await _people(db_session)
    workspace, _ = await seed_workspace(db_session, user_id=ana.id, slug="sales")

    def run(sql: str, *, status: str = "done", finished: bool = True, **fields) -> Query:
        return Query(
            workspace_id=workspace.id,
            user_id=ana.id,
            sql=sql,
            status=status,
            started_at=NOW,
            finished_at=NOW if finished else None,
            **fields,
        )

    db_session.add_all(
        [
            run("SELECT 'done';"),
            run("SELECT 'failed';", status="failed"),
            run("SELECT 'cancelled';", status="cancelled"),
            run("SELECT 'dbt';", origin="session"),
            run("SELECT 'scheduled';", origin="scheduled"),
            run("SELECT 'running';", status="running", finished=False),
            run("SELECT 'queued';", status="queued", finished=False),
            run("SELECT 'sample';", origin="sample"),
            run("SELECT 'metadata';", origin="metadata"),
            run("SELECT 'maintenance';", origin="maintenance"),
            run("SELECT 'describe';", origin="describe"),
        ]
    )
    await db_session.commit()

    rows = await history_rows(db_session)

    assert sorted(r.sql for r in rows) == [
        "SELECT 'cancelled';",
        "SELECT 'dbt';",
        "SELECT 'done';",
        "SELECT 'failed';",
        "SELECT 'scheduled';",
    ]
    assert {(r.workspace_slug, r.user_email) for r in rows} == {("sales", "ana@example.com")}
    # Every column the copy has, and nothing it keeps out (results, profiles, probes).
    assert set(rows[0]._mapping) == {c.name for c in store.HISTORY.columns}


async def test_history_reads_only_what_is_inside_the_retention_window(db_session):
    ana, _ = await _people(db_session)
    workspace, _ = await seed_workspace(db_session, user_id=ana.id)
    db_session.add_all(
        [
            Query(
                workspace_id=workspace.id,
                sql=f"SELECT {n};",
                status="done",
                started_at=NOW,
                finished_at=NOW - timedelta(days=n),
            )
            for n in (1, 400)
        ]
    )
    await db_session.commit()

    rows = await history_rows(db_session, floor=NOW - timedelta(days=365))

    assert [r.sql for r in rows] == ["SELECT 1;"]


async def test_grants_list_every_kind_of_access(db_session):
    ana, robot = await _people(db_session)
    workspace, catalog = await seed_workspace(db_session, user_id=ana.id, slug="sales")
    agent = Agent(name="agent-a", access_mode="scoped")
    db_session.add(agent)
    await db_session.flush()
    workspace.default_agent_id = agent.id
    db_session.add_all(
        [
            CatalogGrant(
                user_id=robot.id,
                catalog_id=catalog.id,
                schema_name="analytics",
                table_name="orders",
                tier="reader",
                created_by=ana.id,
            ),
            AgentGrant(agent_id=agent.id, user_id=robot.id, tier="use", created_by=ana.id),
            AgentGrant(agent_id=agent.id, workspace_id=workspace.id, tier="use"),
        ]
    )
    await db_session.commit()

    rows = await grant_rows(db_session, NOW)

    def kind(name: str) -> list[dict]:
        return [r for r in rows if r["kind"] == name]

    assert {
        (r["principal_email"], r["principal_type"], r["level"]) for r in kind("global_role")
    } == {
        ("ana@example.com", "user", "admin"),
        ("robot@service-account.local", "service_account", "user"),
    }
    assert [
        (r["principal_email"], r["workspace_slug"], r["level"]) for r in kind("workspace_member")
    ] == [("ana@example.com", "sales", "owner")]
    (attachment,) = kind("catalog_attachment")
    assert (attachment["catalog_slug"], attachment["level"], attachment["granted_by_email"]) == (
        catalog.slug,
        "open",
        "ana@example.com",
    )
    (grant,) = kind("catalog_grant")
    assert (
        grant["principal_email"],
        grant["schema_name"],
        grant["table_name"],
        grant["level"],
    ) == (
        "robot@service-account.local",
        "analytics",
        "orders",
        "reader",
    )
    assert grant["granted_by_email"] == "ana@example.com"
    assert [(r["agent_name"], r["level"]) for r in kind("agent_access_mode")] == [
        ("agent-a", "scoped")
    ]
    assert sorted(
        (r["principal_type"], r["principal_email"], r["workspace_slug"])
        for r in kind("agent_grant")
    ) == [
        ("service_account", "robot@service-account.local", None),
        ("workspace", None, "sales"),
    ]
    assert [
        (r["workspace_slug"], r["agent_name"], r["level"]) for r in kind("workspace_default_agent")
    ] == [("sales", "agent-a", "use")]
    # Each row carries every column of access.grants, stamped with the snapshot time.
    assert all(set(r) == {c.name for c in store.GRANTS.columns} for r in rows)
    assert {r["snapshot_time"] for r in rows} == {NOW}


async def test_grants_name_a_deactivated_principal_as_such(db_session):
    ana, _ = await _people(db_session)
    ana.is_active = False
    await db_session.commit()

    rows = await grant_rows(db_session, NOW)

    assert [r["principal_active"] for r in rows if r["principal_email"] == ana.email] == [False]


async def test_a_workspace_without_default_compute_grants_none(db_session):
    db_session.add(Workspace(id=uuid.uuid4(), slug="empty", name="Empty"))
    await db_session.commit()

    assert not [
        r for r in await grant_rows(db_session, NOW) if r["kind"] == "workspace_default_agent"
    ]
