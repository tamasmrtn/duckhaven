"""Acting for someone: every access check holds for both principals.

The assistant's per-turn token authenticates as its service account and acts for
the person whose turn it is (``services.delegation``). Each check here is asked
for the account alone, which must answer as it always has, and then delegated,
where the lower of the two answers wins.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from conftest import seed_workspace
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.catalog import WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.query import Query
from api.models.result_cache import ResultCacheEntry
from api.models.semantic import SemanticDataset, SemanticModel
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services import agent_access, delegation, grants, rbac
from api.services import workspace as workspace_service
from api.services.agent_registry import registry
from api.services.delegation import Delegation
from api.services.grants import GrantDenied
from api.services.permissions import Permission
from api.services.query import dispatch_query
from api.services.result_cache.key import CacheContext
from api.services.result_cache.service import HIT, Lookup, serve_hit
from api.services.semantic import store as semantic_store

SQL = "SELECT * FROM analytics.orders"


@pytest_asyncio.fixture
async def account(db_session):
    sa = User(
        email="assistant@service-account.local",
        name="Assistant",
        role="user",
        auth_provider="service_account",
    )
    db_session.add(sa)
    await db_session.commit()
    await db_session.refresh(sa)
    return sa


@pytest_asyncio.fixture
async def person(db_session):
    human = User(email="pat@people.local", name="Pat", role="user")
    db_session.add(human)
    await db_session.commit()
    await db_session.refresh(human)
    return human


@pytest_asyncio.fixture
async def scoped(db_session, person):
    """A workspace with no members yet whose one catalog is scoped."""
    ws, cat = await seed_workspace(db_session, user_id=person.id, role=None)
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id)
        .values(access_mode="scoped")
    )
    await db_session.commit()
    return ws, cat


def _member(db, ws, user, role):
    if role is not None:
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))


def _grant(db, user, cat, tier, *, schema="analytics", table=None):
    if tier is not None:
        db.add(
            CatalogGrant(
                user_id=user.id, catalog_id=cat.id, schema_name=schema, table_name=table, tier=tier
            )
        )


def _act_for(db, account, person):
    delegation.bind(db, Delegation(actor_id=account.id, delegator_id=person.id))


# --- Who a check holds for ---------------------------------------------------


async def test_a_check_holds_for_the_account_alone_without_a_delegation(db_session, account):
    assert delegation.principals_for(db_session, account.id) == (account.id,)


async def test_a_delegation_adds_the_person_to_checks_for_the_account_only(
    db_session, account, person
):
    someone_else = uuid.uuid4()
    _act_for(db_session, account, person)

    assert delegation.principals_for(db_session, account.id) == (account.id, person.id)
    # A check about anyone else, such as the target of a new grant, is unchanged.
    assert delegation.principals_for(db_session, someone_else) == (someone_else,)

    delegation.bind(db_session, None)
    assert delegation.principals_for(db_session, account.id) == (account.id,)


async def test_acting_for_restores_whatever_was_bound_before(db_session, account, person):
    earlier = Delegation(actor_id=account.id, delegator_id=uuid.uuid4())
    delegation.bind(db_session, earlier)

    with delegation.acting_for(db_session, account.id, person.id):
        assert delegation.bound(db_session) == Delegation(account.id, person.id)
    assert delegation.bound(db_session) == earlier

    # No person, no change: a run made for no one is checked as before.
    with delegation.acting_for(db_session, account.id, None):
        assert delegation.bound(db_session) == earlier


# --- Scoped catalog grants ----------------------------------------------------


@pytest.mark.parametrize(
    ("account_tier", "person_tier", "person_role", "expected"),
    [
        ("writer", "reader", "writer", "reader"),
        ("reader", "writer", "writer", "reader"),
        ("metadata", "writer", "writer", "metadata"),
        ("writer", None, "writer", None),
        (None, "writer", "writer", None),
        # The person's role caps their grant, as it always has.
        ("writer", "writer", "reader", "reader"),
        # A person outside the workspace has no tier, whatever they were granted.
        ("writer", "writer", None, None),
    ],
)
async def test_a_delegated_tier_is_the_lower_of_the_two(
    db_session, account, person, scoped, account_tier, person_tier, person_role, expected
):
    ws, cat = scoped
    _member(db_session, ws, account, "writer")
    _member(db_session, ws, person, person_role)
    _grant(db_session, account, cat, account_tier)
    _grant(db_session, person, cat, person_tier)
    await db_session.commit()

    tier = await grants.node_tier(db_session, ws.id, cat, account.id, "analytics", "orders")
    assert tier == account_tier

    _act_for(db_session, account, person)
    tier = await grants.node_tier(db_session, ws.id, cat, account.id, "analytics", "orders")
    assert tier == expected


async def test_listings_show_only_what_both_can_discover(db_session, account, person, scoped):
    ws, cat = scoped
    _member(db_session, ws, account, "reader")
    _member(db_session, ws, person, "reader")
    _grant(db_session, account, cat, "reader", schema=None)  # the whole catalog
    _grant(db_session, person, cat, "reader", table="orders")
    await db_session.commit()
    schemas, tables = ["analytics", "finance"], ["orders", "customers"]

    async def listed():
        return (
            await grants.visible_schemas(db_session, ws.id, cat, account.id, schemas),
            await grants.visible_tables(db_session, ws.id, cat, account.id, "analytics", tables),
        )

    assert await listed() == ({"analytics", "finance"}, {"orders", "customers"})
    _act_for(db_session, account, person)
    assert await listed() == ({"analytics"}, {"orders"})


async def test_a_schema_the_person_cannot_reach_is_refused(db_session, account, person, scoped):
    ws, cat = scoped
    _member(db_session, ws, account, "reader")
    _member(db_session, ws, person, "reader")
    _grant(db_session, account, cat, "reader", schema=None)
    _grant(db_session, person, cat, "reader")  # analytics only
    await db_session.commit()
    _act_for(db_session, account, person)

    await grants.assert_chosen_schema_reachable(
        db_session, ws.id, account.id, cat.slug, "analytics", [cat]
    )
    with pytest.raises(GrantDenied):
        await grants.assert_chosen_schema_reachable(
            db_session, ws.id, account.id, cat.slug, "finance", [cat]
        )


async def test_a_query_the_person_may_not_run_is_refused(db_session, account, person, scoped):
    ws, cat = scoped
    _member(db_session, ws, account, "reader")
    _member(db_session, ws, person, "reader")
    _grant(db_session, account, cat, "reader")
    await db_session.commit()

    await grants.assert_query_access(db_session, ws.id, account.id, SQL, cat.slug, [cat])
    _act_for(db_session, account, person)
    with pytest.raises(GrantDenied):
        await grants.assert_query_access(db_session, ws.id, account.id, SQL, cat.slug, [cat])


async def test_a_cached_result_the_person_may_not_read_is_not_served(
    db_session, account, person, scoped
):
    """The cache is shared by the workspace; the right to read its tables is not."""
    ws, cat = scoped
    _member(db_session, ws, account, "reader")
    _member(db_session, ws, person, "reader")
    _grant(db_session, account, cat, "reader")
    await db_session.commit()
    _act_for(db_session, account, person)
    hit = Lookup(
        status=HIT,
        entry=ResultCacheEntry(),
        context=CacheContext(
            current_catalog=cat.slug, current_schema="analytics", runtime_id=None, timezone="UTC"
        ),
    )
    query = Query(workspace_id=ws.id, user_id=account.id, sql=SQL)

    with pytest.raises(GrantDenied):
        await serve_hit(
            db_session, hit, query, principal_id=account.id, catalogs=[cat], polaris=None
        )
    assert query.status != "done"


async def test_a_semantic_model_on_a_table_the_person_cannot_see_is_hidden(
    db_session, account, person, scoped
):
    ws, cat = scoped
    _member(db_session, ws, account, "reader")
    _member(db_session, ws, person, "reader")
    _grant(db_session, account, cat, "reader")
    model = SemanticModel(workspace_id=ws.id, slug="sales", name="Sales", status="published")
    db_session.add(model)
    await db_session.flush()
    db_session.add(
        SemanticDataset(
            model_id=model.id,
            name="orders",
            catalog_id=cat.id,
            schema_name="analytics",
            table_name="orders",
        )
    )
    await db_session.commit()

    async def visible():
        return await semantic_store.visible(
            db_session, model, workspace_id=ws.id, user_id=account.id, catalogs=[cat]
        )

    assert await visible() is True
    _act_for(db_session, account, person)
    assert await visible() is False


# --- Workspace membership -----------------------------------------------------


@pytest.mark.parametrize(
    ("account_role", "person_role", "min_role", "expected"),
    [
        ("writer", "writer", "writer", "writer"),
        ("owner", "reader", "reader", "reader"),
        ("reader", "owner", "reader", "reader"),
        ("writer", "reader", "writer", None),
        ("reader", "owner", "writer", None),
        ("writer", None, "reader", None),
        (None, "owner", "reader", None),
    ],
)
async def test_both_must_be_members_and_the_lower_role_applies(
    db_session, account, person, scoped, account_role, person_role, min_role, expected
):
    ws, _cat = scoped
    _member(db_session, ws, account, account_role)
    _member(db_session, ws, person, person_role)
    await db_session.commit()
    _act_for(db_session, account, person)

    if expected is None:
        with pytest.raises(Exception) as refused:
            await workspace_service.assert_workspace_member(db_session, ws.id, account.id, min_role)
        assert refused.value.status_code == 403
    else:
        member = await workspace_service.assert_workspace_member(
            db_session, ws.id, account.id, min_role
        )
        assert member.role == expected


async def test_the_account_alone_keeps_its_own_role(db_session, account, person, scoped):
    ws, _cat = scoped
    _member(db_session, ws, account, "owner")
    await db_session.commit()

    member = await workspace_service.assert_workspace_member(db_session, ws.id, account.id, "owner")
    assert member.role == "owner"


async def test_workspace_listings_show_only_the_ones_both_belong_to(
    db_session, account, person, scoped
):
    shared, _cat = scoped
    _member(db_session, shared, account, "reader")
    _member(db_session, shared, person, "reader")
    other, _ = await seed_workspace(db_session, user_id=account.id, slug="other", role="reader")
    await db_session.commit()

    async def listed():
        rows = await db_session.execute(
            select(Workspace.slug).where(workspace_service.member_of(db_session, account.id))
        )
        return set(rows.scalars())

    assert await listed() == {shared.slug, other.slug}
    _act_for(db_session, account, person)
    assert await listed() == {shared.slug}


# --- Global permissions -------------------------------------------------------


async def test_a_delegated_request_holds_only_the_permissions_both_roles_grant(
    db_session, account, person
):
    person.role = "admin"
    await db_session.commit()

    assert await rbac.has_permission(db_session, person, Permission.AGENTS_MANAGE)
    _act_for(db_session, account, person)
    assert await rbac.user_permissions(db_session, account) == set()

    # The other way round: an admin account acting for an ordinary user.
    account.role, person.role = "admin", "user"
    await db_session.commit()
    assert await rbac.user_permissions(db_session, account) == set()
    delegation.bind(db_session, None)
    assert await rbac.has_permission(db_session, account, Permission.AGENTS_MANAGE)


# --- Compute ------------------------------------------------------------------


@pytest_asyncio.fixture
async def restricted_agent(db_session):
    agent = Agent(name="locked", status="healthy", access_mode="restricted")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    return agent


@pytest.mark.parametrize(
    ("account_tier", "person_tier", "expected"),
    [("operate", "use", "use"), ("use", "admin", "use"), ("operate", None, None)],
)
async def test_an_agent_is_usable_at_the_lower_of_the_two_tiers(
    db_session, account, person, restricted_agent, account_tier, person_tier, expected
):
    for user, tier in ((account, account_tier), (person, person_tier)):
        if tier is not None:
            db_session.add(AgentGrant(agent_id=restricted_agent.id, user_id=user.id, tier=tier))
    await db_session.commit()

    assert await agent_access.agent_tier(db_session, account, restricted_agent) == account_tier
    _act_for(db_session, account, person)
    assert await agent_access.agent_tier(db_session, account, restricted_agent) == expected


async def test_a_workspace_default_agent_needs_both_to_be_members(
    db_session, account, person, scoped, restricted_agent
):
    ws, _cat = scoped
    ws.default_agent_id = restricted_agent.id
    _member(db_session, ws, account, "reader")
    await db_session.commit()

    async def embedded():
        return await agent_access.is_workspace_default_for(
            db_session, account.id, ws, restricted_agent.id
        )

    assert await embedded() is True
    _act_for(db_session, account, person)
    assert await embedded() is False
    _member(db_session, ws, person, "reader")
    await db_session.commit()
    assert await embedded() is True


# --- Runs ---------------------------------------------------------------------


async def test_a_run_made_while_acting_for_someone_records_them(
    db_session, account, person, scoped
):
    ws, _cat = scoped
    _act_for(db_session, account, person)
    by_the_account = Query(workspace_id=ws.id, user_id=account.id, sql="SELECT 1")
    by_someone_else = Query(workspace_id=ws.id, user_id=person.id, sql="SELECT 1")
    db_session.add_all([by_the_account, by_someone_else])
    await db_session.flush()

    assert by_the_account.on_behalf_of_id == person.id
    assert by_someone_else.on_behalf_of_id is None


async def test_a_run_made_for_no_one_records_no_one(db_session, account, scoped):
    ws, _cat = scoped
    query = Query(workspace_id=ws.id, user_id=account.id, sql="SELECT 1")
    db_session.add(query)
    await db_session.flush()

    assert query.on_behalf_of_id is None


class _Socket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def connected_agent(db_session):
    agent = Agent(
        name="agent",
        status="healthy",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"]},
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    socket = _Socket()
    registry.register(agent.id, socket)  # type: ignore[arg-type]
    yield agent, socket
    registry.unregister(agent.id)


async def test_a_parked_run_is_checked_for_its_person_when_replayed(
    db_engine, db_session, account, person, scoped, connected_agent
):
    """Replay happens outside the request that made the run, in a session that is
    acting for no one, so the person comes from the row."""
    ws, cat = scoped
    agent, socket = connected_agent
    _member(db_session, ws, account, "reader")
    _member(db_session, ws, person, "reader")
    _grant(db_session, account, cat, "reader")
    parked = Query(
        workspace_id=ws.id,
        agent_id=agent.id,
        user_id=account.id,
        on_behalf_of_id=person.id,
        sql=SQL,
    )
    db_session.add(parked)
    await db_session.commit()

    async with async_sessionmaker(db_engine, expire_on_commit=False)() as replay:
        query = await replay.get(Query, parked.id)
        with pytest.raises(GrantDenied):
            await dispatch_query(replay, query, principal_id=account.id)
        assert delegation.bound(replay) is None
    assert socket.sent == []

    # The same run made for no one is the account's alone, and goes out.
    parked.on_behalf_of_id = None
    await db_session.commit()
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as replay:
        query = await replay.get(Query, parked.id)
        await dispatch_query(replay, query, principal_id=account.id)
    assert len(socket.sent) == 1


# --- Coverage -------------------------------------------------------------------

# The checks every route reaches. Each asks `principals_for` who it must hold for;
# the coverage below keeps a new path to the same rows from going around them.
CHOKE_POINTS = [
    grants.node_tier,
    grants.visible_schemas,
    grants.visible_tables,
    grants.assert_chosen_schema_reachable,
    workspace_service.assert_workspace_member,
    workspace_service.member_of,
    rbac.user_permissions,
    agent_access.visible_tiers,
    agent_access.is_workspace_default_for,
]


@pytest.mark.parametrize("check", CHOKE_POINTS, ids=lambda check: check.__qualname__)
def test_every_choke_point_asks_who_it_must_hold_for(check):
    assert "principals_for(" in inspect.getsource(check)


SRC = Path(__file__).resolve().parents[3] / "src" / "api"

# Every function that reads one user's memberships or grants. Reading the caller's
# rows anywhere but through a choke point would let a delegated request reach what
# only its account may, so a new entry belongs in one of these groups on purpose.
PER_USER_LOOKUPS = {
    # Choke points, which loop over `principals_for`.
    "services/workspace.py::assert_workspace_member",
    "services/workspace.py::member_of",
    "services/agent_access.py::is_workspace_default_for",
    # One principal's rows, read only by the choke points (see the next test).
    "services/grants.py::_member_role",
    "services/grants.py::load_grants",
    "services/agent_access.py::load_caller_grants",
    # The assistant's write hint, which the runner asks for the account and the
    # person in turn.
    "services/assistant/access.py::_workspace_member",
    "services/assistant/access.py::service_account_can_write",
    # About the user being administered, not the caller.
    "routers/admin/agent_access.py::upsert_agent_grant",
    "routers/admin/service_accounts.py::delete_service_account",
    "routers/admin/users.py::list_user_workspaces",
    "routers/admin/users.py::remove_user_from_workspace",
    "routers/grants.py::upsert_grant",
}
_ACCESS_MODELS = {"WorkspaceMember", "CatalogGrant", "AgentGrant"}


def _functions():
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                yield f"{path.relative_to(SRC)}::{node.name}", node


def _is_user_id(node) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "user_id"
        and isinstance(node.value, ast.Name)
        and node.value.id in _ACCESS_MODELS
    )


def _is_user_pk(node) -> bool:
    """``User.id``: a join to the users table, not a filter on one user."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "id"
        and isinstance(node.value, ast.Name)
        and node.value.id == "User"
    )


def test_no_check_reads_one_users_access_rows_unaccounted_for():
    found = set()
    for name, fn in _functions():
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Eq)):
                continue
            left, right = node.left, node.comparators[0]
            if (_is_user_id(left) and not _is_user_pk(right)) or (
                _is_user_id(right) and not _is_user_pk(left)
            ):
                found.add(name)
    assert found == PER_USER_LOOKUPS


def test_one_principals_rows_are_read_only_by_the_choke_points():
    helpers = {"_member_role", "load_grants", "load_caller_grants"}
    callers = set()
    for name, fn in _functions():
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if called in helpers:
                    callers.add(name)
    assert callers == {
        "services/grants.py::node_tier",
        "services/grants.py::visible_schemas",
        "services/grants.py::visible_tables",
        "services/grants.py::assert_chosen_schema_reachable",
        "services/agent_access.py::_visible_tiers_of",
    }
