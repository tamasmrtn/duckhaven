from api.models.user import User
from api.services.permissions import Permission
from api.services.provisioning import resolve_role
from api.services.rbac import has_permission, seed_roles, user_permissions


async def _make_user(db_session, role: str) -> User:
    user = User(email=f"{role}@x.local", name=role, password_hash="x", role=role)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_admin_has_all_permissions(db_session):
    admin = await _make_user(db_session, "admin")
    perms = await user_permissions(db_session, admin)
    assert perms == {p.value for p in Permission}
    assert await has_permission(db_session, admin, Permission.USERS_MANAGE)


async def test_user_has_no_global_permissions(db_session):
    user = await _make_user(db_session, "user")
    assert await user_permissions(db_session, user) == set()
    assert not await has_permission(db_session, user, Permission.USERS_MANAGE)


async def test_seed_roles_is_idempotent(db_session):
    # The conftest already seeded; a second call must not duplicate permissions.
    await seed_roles(db_session)
    admin = await _make_user(db_session, "admin")
    assert len(await user_permissions(db_session, admin)) == len(set(Permission))


def test_resolve_role_admin_wins():
    role_map = {"dh-admins": "admin", "dh-users": "user"}
    assert resolve_role(["dh-users", "dh-admins"], role_map) == "admin"


def test_resolve_role_defaults_to_user():
    assert resolve_role(["unmapped"], {"dh-admins": "admin"}) == "user"
    assert resolve_role([], {}) == "user"
