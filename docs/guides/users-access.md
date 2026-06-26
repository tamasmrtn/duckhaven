# Manage users & access

DuckHaven has two independent layers of access: a **global role** (what someone can administer) and per-workspace
[membership](../concepts/workspaces.md) (which data they can touch). This guide covers both. For the underlying model
see [Identity & permissions](../concepts/permissions.md).

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

Access to data is granted by adding a user to a workspace with a role:

| Role | Grants |
|---|---|
| `reader` | Browse schemas and tables; run queries; view history |
| `writer` | Reader, plus create/modify/drop tables and run DDL |
| `owner` | Writer, plus manage workspace membership |

Workspace membership is always managed inside DuckHaven (it is not driven by IdP groups). Authorization is enforced at
the API boundary before any query reaches an agent. See [Permissions](../concepts/permissions.md).

## Registering agents

Agents authenticate separately, with one-time bootstrap tokens rather than user accounts. See
[Add an agent](../deployment/add-agent.md).
