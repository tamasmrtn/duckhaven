# Manage users & access

This guide is the **how-to** for the first two of DuckHaven's [three access layers](access-levels.md): a user's
**global role** (what they can administer) and their **workspace membership** (which workspaces they can enter). The
third layer — fine-grained data grants inside a *scoped* catalog — is managed from the catalog view; see
[How access works](access-levels.md) for the model and [Identity & permissions](../concepts/permissions.md) for the
full reference.

## Users

The first admin is created during [installation](../getting-started/installation.md) from a one-shot setup token.
Admins manage everyone else from **Admin → Users**.

### Add a local user

**Admin → Users → Add user** creates a local (password) account: enter an email, name, temporary password, and a global
role (`admin` or `user`). Federated users do **not** need to be added here — they are provisioned automatically the
first time they sign in through [SSO](connect-idp.md) or [LDAP](connect-ldap.md).

### Change a global role

Use the role selector on a user's row. For SSO/LDAP users the role is re-synced from your IdP's group mapping on their
next sign-in, so the durable way to change a federated user's role is to change their group membership in the IdP — see
[Connect an IdP](connect-idp.md#map-groups-to-roles).

### Deactivate or sign someone out

The **⋯** menu on a user's row offers:

- **Deactivate** — blocks the account immediately. Any live session is rejected on its next request, and the user
  cannot sign in again (locally or via SSO/LDAP) until reactivated. The last remaining admin cannot be deactivated.
- **Revoke sessions** — force-logs-out the user without disabling the account (e.g. after a lost laptop). They can sign
  in again.

See [Offboarding & break-glass](../operations/offboarding.md) for the full departure checklist.

## Workspace membership and roles

Access to a workspace's data starts with adding a user to it with a role:

| Role | Grants |
|---|---|
| `reader` | Browse schemas and tables; run queries; view history |
| `writer` | Reader, plus create/modify/drop tables and run DDL |
| `owner` | Writer, plus manage workspace membership |

There are two ways to manage membership:

- **From Admin → Users** — open a user's **⋯ → Manage workspaces** to grant, change, or remove their role in any
  workspace from one place. This is the admin path and works for any workspace (it requires the global `users:manage`
  permission, not workspace ownership).
- **Within a workspace** — an `owner` of a workspace manages that workspace's own members.

Workspace membership is always managed inside DuckHaven (it is not driven by IdP groups, which set only the global
role). Authorization is enforced at the API boundary before any query reaches an agent. See
[Permissions](../concepts/permissions.md).

By default a member's role applies to every table in the workspace's catalogs. To narrow access to specific schemas or
tables, switch a catalog to *scoped* mode and grant per-object access — the third access layer, covered in
[How access works](access-levels.md).

## Registering agents

Agents authenticate separately, with one-time bootstrap tokens rather than user accounts. See
[Add an agent](../deployment/add-agent.md).

### Who can use an agent

Any signed-in user can run work on any agent by default. To reserve one — or to let someone restart an agent without
making them a deployment-wide admin — open **Compute → *an agent* → Access**, set the agent to **restricted**,
and grant `use`, `operate`, or `admin` to a user or to a whole workspace. A workspace grant follows membership, so
adding someone to the workspace gives them the agent with no further step. See
[Per-agent access](../concepts/permissions.md#per-agent-access).
