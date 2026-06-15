# Manage users & access

DuckHaven uses local accounts and per-[workspace](../concepts/workspaces.md) roles. This guide covers adding people and
granting access.

## Users

The first admin is created during [installation](../getting-started/installation.md) from a one-shot setup token.
Admins manage the user list from **Admin → Users**.

Authentication is local accounts with session cookies (bcrypt-hashed passwords, seven-day sessions). There is no
SSO/LDAP integration.

## Workspace membership and roles

Access is granted by adding a user to a workspace with a role:

| Role | Grants |
|---|---|
| `reader` | Browse schemas and tables; run queries; view history |
| `writer` | Reader, plus create/modify/drop tables and run DDL |
| `owner` | Writer, plus manage workspace membership |

Authorization is enforced at the API boundary before any query reaches an agent. See
[Permissions](../concepts/permissions.md).

## Registering agents

Agents authenticate separately, with one-time bootstrap tokens rather than user accounts. See
[Add an agent](../deployment/add-agent.md).
