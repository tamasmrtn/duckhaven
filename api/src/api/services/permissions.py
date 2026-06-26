from enum import StrEnum


class Permission(StrEnum):
    """Enumerated global permissions enforced at the API boundary.

    Values are stored verbatim in the ``role_permissions`` table and checked by
    ``services.rbac.has_permission``. Adding a permission here is the first step
    to gating a new admin capability; wire it onto a role in
    ``SYSTEM_ROLE_PERMISSIONS`` so it is granted on seed.
    """

    AGENTS_MANAGE = "agents:manage"
    STORAGE_MANAGE = "storage:manage"
    USERS_MANAGE = "users:manage"
    MAINTENANCE_MANAGE = "maintenance:manage"
    CATALOGS_ADMIN = "catalogs:admin"
    QUERIES_ADMIN = "queries:admin"


# Canonical definition of the built-in (system) roles. The database tables are
# seeded from this mapping (`services.rbac.seed_roles`) so a security reviewer
# can inspect roles/permissions in the schema, while code remains the source of
# truth for the two roles DuckHaven ships with. `admin` holds every permission;
# `user` is an authenticated account with no global powers (workspace roles are
# enforced separately by `services.workspace`).
SYSTEM_ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "admin": set(Permission),
    "user": set(),
}
