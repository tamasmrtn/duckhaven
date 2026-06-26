from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.rbac import Role, RolePermission
from api.models.user import User
from api.services.permissions import SYSTEM_ROLE_PERMISSIONS, Permission


async def user_permissions(db: AsyncSession, user: User) -> set[str]:
    """Return the set of permission strings granted to ``user`` via its role."""
    result = await db.execute(
        select(RolePermission.permission)
        .join(Role, RolePermission.role_id == Role.id)
        .where(Role.name == user.role)
    )
    return set(result.scalars().all())


async def has_permission(db: AsyncSession, user: User, permission: Permission) -> bool:
    return permission.value in await user_permissions(db, user)


async def seed_roles(db: AsyncSession) -> None:
    """Idempotently ensure the built-in roles and their permissions exist.

    Runs on API startup (and in tests) so the schema always matches the code in
    ``SYSTEM_ROLE_PERMISSIONS`` even if the migration seed was skipped. Existing
    custom roles are left untouched; only the system roles are reconciled.
    """
    for name, permissions in SYSTEM_ROLE_PERMISSIONS.items():
        result = await db.execute(select(Role).where(Role.name == name))
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(name=name, description=f"Built-in {name} role", is_system=True)
            db.add(role)
            await db.flush()

        existing = await db.execute(
            select(RolePermission.permission).where(RolePermission.role_id == role.id)
        )
        have = set(existing.scalars().all())
        want = {p.value for p in permissions}
        for perm in want - have:
            db.add(RolePermission(role_id=role.id, permission=perm))
        for perm in have - want:
            await db.execute(
                RolePermission.__table__.delete().where(
                    RolePermission.role_id == role.id,
                    RolePermission.permission == perm,
                )
            )
    await db.commit()
