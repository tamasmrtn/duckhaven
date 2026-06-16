# Permissions

DuckHaven authenticates users with session cookies and authorizes them per [workspace](workspaces.md) with a small set
of roles. Authorization is enforced at the API boundary before any query reaches an [agent](agents.md).

## Authentication

- **Session cookies.** Passwords are hashed with bcrypt; sessions last seven days.
- **First admin.** The first account is created from the setup screen using a one-shot setup token generated on first
  boot. See the [Quickstart](../getting-started/quickstart.md).

## Workspace roles

Each member of a workspace has one role:

| Role | Can do |
|---|---|
| `reader` | List and browse schemas and tables; run queries; view history |
| `writer` | Everything a reader can, plus create/modify/drop tables and run DDL |
| `owner` | Everything a writer can, plus manage workspace membership |

## What is not in scope

- **No row- or column-level security.** Permissions are workspace-level.
- **No SSO/LDAP.** Authentication is local accounts only.
- **Polaris grants are defense-in-depth.** DuckHaven is the sole permission authority; the API check
  (`assert_workspace_member`) is the primary gate.

## Related

- [Manage users & access](../guides/users-access.md) — add members and assign roles.
- [Add an agent](../deployment/add-agent.md) — bootstrap tokens for registering agents.
