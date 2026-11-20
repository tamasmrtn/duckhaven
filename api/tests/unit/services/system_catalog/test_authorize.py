"""Who may read the `duckhaven` system catalog in SQL, and how a statement may name it."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from audit_helpers import audit_events
from conftest import seed_workspace

from api.models.catalog import WorkspaceCatalog
from api.models.query import Query
from api.models.rbac import Role, RolePermission
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services import delegation
from api.services import grants as grant_service
from api.services.permissions import Permission
from api.services.query import assert_result_access
from api.services.system_catalog import authorize
from api.services.system_catalog.authorize import (
    FULL_NAMES,
    NOT_CURRENT,
    READ_ONLY,
    UNPARSEABLE,
    reads_system_catalog,
)


async def _user(db_session, email: str, role: str) -> User:
    user = User(email=email, name=email, password_hash="x", role=role)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def admin(db_session) -> User:
    return await _user(db_session, "admin@sys.local", "admin")


@pytest.fixture
async def member(db_session) -> User:
    return await _user(db_session, "member@sys.local", "user")


@pytest.fixture
async def workspace(db_session, admin):
    """An open workspace with one catalog, `sales`."""
    return await seed_workspace(db_session, user_id=admin.id, catalog_slug="sales")


async def _check(db_session, workspace, principal: User | None, sql: str, active: str = "sales"):
    ws, cat = workspace
    await grant_service.assert_query_access(
        db_session, ws.id, principal.id if principal else None, sql, active, [cat]
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM duckhaven.access.audit;",
        "SELECT * FROM duckhaven.access.grants;",
        "SELECT * FROM duckhaven.query.history;",
        'SELECT action FROM "DuckHaven"."Access"."Audit";',
        "SELECT a.action FROM duckhaven.access.audit AS a "
        "JOIN duckhaven.query.history AS h ON h.user_id = a.actor_id;",
        "WITH recent AS (SELECT * FROM duckhaven.access.audit) SELECT count(*) FROM recent;",
        "SELECT * FROM sales.analytics.orders WHERE id IN (SELECT 1 FROM duckhaven.query.history);",
        "DESCRIBE duckhaven.access.audit;",
        "SUMMARIZE duckhaven.query.history;",
        "SELECT 1; SELECT * FROM duckhaven/* the catalog */.access.audit;",
    ],
)
async def test_an_admin_reads_its_tables(db_session, workspace, admin, sql):
    await _check(db_session, workspace, admin, sql)
    assert reads_system_catalog(sql, [workspace[1]])


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sales.analytics.orders WHERE client = 'duckhaven-cli';",
        "SELECT 1; -- copied from duckhaven.access.audit",
        "SELECT * FROM sales.analytics.duckhaven_events;",
        "INSERT INTO sales.analytics.notes VALUES ('moved to duckhaven');",
        # A PIVOT loses its text, which matters only in a script that names it.
        "SET duckhaven_concurrency = 'auto'; PIVOT sales.analytics.orders ON status USING sum(1);",
    ],
)
async def test_the_name_outside_an_identifier_needs_no_permission(
    db_session, workspace, member, sql
):
    await _check(db_session, workspace, member, sql)
    assert not reads_system_catalog(sql, [workspace[1]])


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        # Writes would copy it where others can read it.
        ("INSERT INTO sales.analytics.t SELECT * FROM duckhaven.access.audit;", READ_ONLY),
        ("CREATE TABLE sales.analytics.t AS SELECT * FROM duckhaven.access.audit;", READ_ONLY),
        ("CREATE VIEW sales.analytics.v AS SELECT * FROM duckhaven.query.history;", READ_ONLY),
        ("SELECT 1; DELETE FROM duckhaven.access.audit;", READ_ONLY),
        ("EXPLAIN SELECT * FROM duckhaven.access.audit;", READ_ONLY),
        # Later unqualified names would resolve inside it, unchecked.
        ("USE duckhaven;", NOT_CURRENT),
        ("USE duckhaven.access;", NOT_CURRENT),
        ("SET search_path = 'duckhaven.access';", NOT_CURRENT),
        ("SET schema = 'duckhaven.access';", NOT_CURRENT),
        # The attachment shows the whole database, not just the three tables.
        ("SELECT * FROM duckhaven.pg_catalog.pg_roles;", FULL_NAMES),
        ("SELECT * FROM duckhaven.information_schema.tables;", FULL_NAMES),
        ("SELECT * FROM duckhaven._state.cursors;", FULL_NAMES),
        ("SELECT * FROM duckhaven.public.anything;", FULL_NAMES),
        ("SELECT * FROM duckhaven.access.secrets;", FULL_NAMES),
        # A two-part name could bind to its default schema.
        ("SELECT * FROM duckhaven.audit;", FULL_NAMES),
        ("SHOW TABLES FROM duckhaven.access;", FULL_NAMES),
        ("SELECT duckhaven FROM sales.analytics.orders;", FULL_NAMES),
        ("SELECT * FROM sales.analytics.orders AS duckhaven;", FULL_NAMES),
        ("PIVOT duckhaven.access.audit ON action USING count(*);", UNPARSEABLE),
        # Expanded by DuckDB into statements that come back without their text, or
        # with a truncated rewrite of it when the script ends in a semicolon.
        ("PIVOT duckhaven.access.audit ON action USING count(*)", UNPARSEABLE),
        ("SELECT * FROM (PIVOT duckhaven.access.audit ON outcome USING count(*))", UNPARSEABLE),
        (
            "SELECT 1; SELECT * FROM (PIVOT duckhaven.access.grants ON kind USING count(*))",
            UNPARSEABLE,
        ),
        ("SELECT * FROM (PIVOT duckhaven.access.audit ON outcome USING count(*));", UNPARSEABLE),
        (
            "WITH p AS (PIVOT duckhaven.access.audit ON outcome USING count(*)) SELECT * FROM p;",
            UNPARSEABLE,
        ),
        ("SELECT * FROM duckhaven.access.audit WHERE (;", UNPARSEABLE),
    ],
)
async def test_any_other_use_is_refused_even_to_an_admin(db_session, workspace, admin, sql, reason):
    with pytest.raises(grant_service.GrantDenied, match=reason.split(".")[0]):
        await _check(db_session, workspace, admin, sql)
    assert not reads_system_catalog(sql, [workspace[1]])


async def test_it_is_never_the_current_catalog(db_session, workspace, admin):
    with pytest.raises(grant_service.GrantDenied, match="current catalog"):
        await _check(db_session, workspace, admin, "SELECT 1;", active="DuckHaven")


async def test_a_member_without_the_permission_is_refused_and_recorded(
    db_session, workspace, member
):
    sql = "SELECT * FROM duckhaven.access.audit;"
    with pytest.raises(grant_service.GrantDenied, match="needs the audit:read permission"):
        await _check(db_session, workspace, member, sql)

    (event,) = await audit_events(db_session, "authz.grant_denied")
    assert (event.actor_email, event.details["reason"]) == (
        member.email,
        "Reading duckhaven.access.audit needs the audit:read permission.",
    )


async def _auditor(db_session) -> User:
    """Holds audit:read only."""
    role = Role(name="auditor")
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission=Permission.AUDIT_READ.value))
    return await _user(db_session, "auditor@sys.local", "auditor")


async def test_each_table_takes_its_own_permission(db_session, workspace):
    auditor = await _auditor(db_session)

    await _check(db_session, workspace, auditor, "SELECT * FROM duckhaven.access.audit;")
    await _check(db_session, workspace, auditor, "SELECT * FROM duckhaven.access.grants;")
    with pytest.raises(grant_service.GrantDenied, match="needs the queries:admin permission"):
        await _check(db_session, workspace, auditor, "SELECT * FROM duckhaven.query.history;")


async def test_a_refusal_names_each_table_it_may_not_read(db_session, workspace, member):
    sql = (
        "SELECT * FROM duckhaven.query.history h "
        "JOIN duckhaven.access.audit a ON a.actor_id = h.user_id;"
    )
    with pytest.raises(grant_service.GrantDenied) as exc:
        await _check(db_session, workspace, member, sql)

    assert str(exc.value) == (
        "Reading duckhaven.access.audit needs the audit:read permission; "
        "duckhaven.query.history needs the queries:admin permission."
    )


async def test_what_each_principal_may_read(db_session, admin, member):
    auditor = await _auditor(db_session)

    info = {("info_schema", t) for t in ("catalogs", "schemata", "tables", "columns")}
    audited = {
        *(("access", t) for t in ("audit", "grants", "users", "workspaces", "credentials")),
        ("access", "table_access"),
        ("lineage", "table_lineage"),
        ("lineage", "column_lineage"),
    }
    assert await authorize.readable_by(db_session, admin.id) == authorize.TABLES
    assert await authorize.readable_by(db_session, auditor.id) == {
        *audited,
        *info,
    }
    assert await authorize.readable_by(db_session, member.id) == info
    with delegation.acting_for(db_session, admin.id, auditor.id):
        assert await authorize.readable_by(db_session, admin.id) == {
            *audited,
            *info,
        }


async def test_an_unknown_principal_is_refused(db_session, workspace):
    with pytest.raises(grant_service.GrantDenied, match="needs the audit:read permission"):
        await _check(db_session, workspace, None, "SELECT * FROM duckhaven.access.audit;")


async def test_acting_for_someone_needs_their_permissions_too(db_session, workspace, admin, member):
    sql = "SELECT * FROM duckhaven.query.history;"
    with delegation.acting_for(db_session, admin.id, member.id):
        with pytest.raises(grant_service.GrantDenied, match="needs the queries:admin permission"):
            await _check(db_session, workspace, admin, sql)

    other = await _user(db_session, "other-admin@sys.local", "admin")
    with delegation.acting_for(db_session, admin.id, other.id):
        await _check(db_session, workspace, admin, sql)


async def test_a_colleague_cannot_open_an_admins_result(db_session, workspace, admin, member):
    """A result holds what its SQL read, so reading it takes the same permissions."""
    ws, _cat = workspace
    query = Query(
        workspace_id=ws.id,
        user_id=admin.id,
        sql="SELECT * FROM duckhaven.access.audit;",
        status="done",
        origin="interactive",
        active_catalog="sales",
    )
    db_session.add(query)
    await db_session.commit()

    await assert_result_access(db_session, query, admin.id)
    with pytest.raises(grant_service.GrantDenied, match="needs the audit:read permission"):
        await assert_result_access(db_session, query, member.id)


async def test_a_workspace_catalog_of_that_name_is_read_as_one(db_session, admin, member):
    """One created before the name was reserved: an ordinary catalog, under its grants."""
    ws, cat = await seed_workspace(db_session, user_id=admin.id, catalog_slug="duckhaven")
    sql = "INSERT INTO duckhaven.analytics.t SELECT * FROM duckhaven.analytics.s;"

    await grant_service.assert_query_access(db_session, ws.id, member.id, sql, "duckhaven", [cat])
    assert not reads_system_catalog("SELECT * FROM duckhaven.access.audit;", [cat])


async def _scoped(db_session, workspace) -> None:
    """Make the workspace's catalog scoped, so engine listings are refused in it."""
    ws, cat = workspace
    await db_session.execute(
        sa.update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id, WorkspaceCatalog.catalog_id == cat.id)
        .values(access_mode="scoped")
    )
    await db_session.commit()


async def test_info_schema_is_read_in_a_workspace_with_a_scoped_catalog(
    db_session, workspace, member
):
    """Its rows are filtered to what the member can see, unlike DuckDB's own listing."""
    await _scoped(db_session, workspace)
    ws, cat = workspace
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id, role="reader"))
    await db_session.commit()

    await _check(db_session, workspace, member, "SELECT * FROM duckhaven.info_schema.tables;")
    with pytest.raises(grant_service.GrantDenied, match="cannot be queried"):
        await _check(db_session, workspace, member, "SELECT * FROM information_schema.tables;")


async def test_a_workspace_catalog_of_that_name_does_not_list_its_metadata(
    db_session, admin, member
):
    ws, cat = await seed_workspace(db_session, user_id=admin.id, catalog_slug="duckhaven")
    await _scoped(db_session, (ws, cat))

    with pytest.raises(grant_service.GrantDenied, match="cannot be queried"):
        await grant_service.assert_query_access(
            db_session, ws.id, admin.id, "SELECT * FROM duckhaven.info_schema.tables;", None, [cat]
        )


def test_a_repeated_statement_is_parsed_once():
    """Each dispatch path asks twice: once to check, once to attach."""
    authorize._tables_read.cache_clear()
    sql = "SELECT * FROM duckhaven.access.grants;"
    for _ in range(3):
        authorize._tables_read(sql)
    assert authorize._tables_read.cache_info().hits == 2
