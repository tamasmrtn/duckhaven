"""Every action the audit log records: its name, category, target and label.

The one list the recorder, the docs reference (`docs/reference/audit-events.md`)
and the coverage test share. An action's name never changes once released, and
new actions are only ever added, so a saved filter or a SIEM rule keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    name: str
    category: str
    #: What ``target_id`` names: a user, a grant, a route, ...
    target_type: str
    #: A short past-tense description, shown in the audit page and the docs.
    label: str


_ACTIONS: tuple[Action, ...] = (
    # identity: accounts and their global roles
    Action("setup.first_admin", "identity", "user", "Created the first admin during setup"),
    Action("user.create", "identity", "user", "Created a user"),
    Action("user.provision", "identity", "user", "Created a user at their first sign-in"),
    Action("user.role_change", "identity", "user", "Changed a user's global role"),
    Action("user.deactivate", "identity", "user", "Deactivated a user"),
    Action("user.reactivate", "identity", "user", "Reactivated a user"),
    Action("user.sessions_revoke", "identity", "user", "Signed a user out everywhere"),
    Action("service_account.create", "identity", "user", "Created a service account"),
    Action("service_account.role_change", "identity", "user", "Changed a service account's role"),
    Action("service_account.deactivate", "identity", "user", "Deactivated a service account"),
    Action("service_account.reactivate", "identity", "user", "Reactivated a service account"),
    Action("service_account.delete", "identity", "user", "Deleted a service account"),
    # access: who can reach which workspace, catalog, table and agent
    Action("workspace.create", "access", "workspace", "Created a workspace"),
    Action("workspace.delete", "access", "workspace", "Deleted a workspace"),
    Action(
        "workspace.default_agent_change",
        "access",
        "workspace",
        "Changed a workspace's default compute",
    ),
    Action("workspace_member.add", "access", "workspace_member", "Added a workspace member"),
    Action(
        "workspace_member.role_change",
        "access",
        "workspace_member",
        "Changed a workspace member's role",
    ),
    Action("workspace_member.remove", "access", "workspace_member", "Removed a workspace member"),
    Action("catalog.create", "access", "catalog", "Created a catalog"),
    Action("catalog.attach", "access", "catalog", "Attached a catalog to a workspace"),
    Action("catalog.detach", "access", "catalog", "Detached a catalog from a workspace"),
    Action("catalog.drop", "access", "catalog", "Dropped a catalog"),
    Action("catalog.access_mode_change", "access", "catalog", "Changed a catalog's access mode"),
    Action("catalog.export", "access", "catalog", "Exported a catalog to a new catalog"),
    Action("catalog_grant.create", "access", "catalog_grant", "Granted access to data"),
    Action("catalog_grant.tier_change", "access", "catalog_grant", "Changed a data grant's tier"),
    Action("catalog_grant.delete", "access", "catalog_grant", "Removed a data grant"),
    Action("agent.access_mode_change", "access", "agent", "Changed an agent's access mode"),
    Action("agent_grant.create", "access", "agent_grant", "Granted access to an agent"),
    Action("agent_grant.tier_change", "access", "agent_grant", "Changed an agent grant's tier"),
    Action("agent_grant.delete", "access", "agent_grant", "Removed an agent grant"),
    # credential: tokens and catalog roles
    Action("pat.issue", "credential", "pat", "Issued a personal access token"),
    Action("pat.revoke", "credential", "pat", "Revoked a personal access token"),
    Action(
        "catalog.ducklake_role_rotate",
        "credential",
        "catalog",
        "Rotated a DuckLake catalog's database password",
    ),
    Action(
        "catalog.ducklake_roles_reconcile",
        "credential",
        "catalog",
        "Reconciled every DuckLake catalog's database role",
    ),
    # auth: signing in and out
    Action("auth.sign_in", "auth", "user", "Signed in"),
    Action("auth.sign_in_failed", "auth", "user", "Failed to sign in"),
    Action("auth.sign_out", "auth", "user", "Signed out"),
    Action("auth.rate_limited", "auth", "user", "Was refused sign-in for too many attempts"),
    # authz: requests refused for lack of access
    Action(
        "authz.permission_denied", "authz", "route", "Was refused for lacking a global permission"
    ),
    Action("authz.grant_denied", "authz", "route", "Was refused by a data grant check"),
)

ACTIONS: dict[str, Action] = {a.name: a for a in _ACTIONS}
assert len(ACTIONS) == len(_ACTIONS), "duplicate audit action name"


def render_reference() -> str:
    """The Markdown table of every action, for `docs/reference/audit-events.md`."""
    lines = ["| Action | Category | Target | Records |", "|---|---|---|---|"]
    for a in sorted(_ACTIONS, key=lambda a: (a.category, a.name)):
        lines.append(f"| `{a.name}` | `{a.category}` | `{a.target_type}` | {a.label} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(render_reference(), end="")
