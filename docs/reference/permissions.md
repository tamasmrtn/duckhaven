---
type: reference
---

# Permissions reference

Every global permission, workspace role, data-grant tier and agent tier in DuckHaven, and what each one allows.

How these combine is explained in [How access works](../access/how-access-works.md). The names below are the exact
strings the API, the CLI and the audit log use.

## Global permissions

A global role carries a set of permissions. DuckHaven ships two roles: `admin` holds every permission below, and
`user` holds none. Global permissions are about operating DuckHaven, not about reading data: an admin still needs
workspace membership to query anything.

| Permission | Allows |
|---|---|
| `users:manage` | Create, edit and disable users; change their global role; manage their workspace memberships; revoke their sessions |
| `service_accounts:manage` | Create and delete service accounts, and issue and revoke their tokens |
| `agents:manage` | Create compute and mint bootstrap tokens, and act as `admin` on every agent |
| `storage:manage` | Register, test and delete storage backends |
| `catalogs:admin` | Drop any catalog, migrate its storage, export it, and rotate or reconcile DuckLake catalog roles; without it, only a catalog's creator can |
| `maintenance:manage` | Change the maintenance policy, start a scan, and apply a recommendation (applying also needs workspace `writer`) |
| `queries:admin` | Read query history across workspaces and filter it by another user; list an agent's queries; filter and force-close other people's SQL sessions |

For people who sign in through an identity provider, the global role comes from their group membership on every
sign-in; see [Connect an IdP](../access/connect-idp.md) and [Connect LDAP / AD](../access/connect-ldap.md).

## Workspace roles

Each member of a workspace has one role. By default it applies to every object in every catalog the workspace attaches.

| Role | Allows |
|---|---|
| `reader` | Browse schemas and tables, run queries, and see the workspace's history |
| `writer` | Everything `reader` allows, plus create, change and drop tables, run DDL and DML, and import lineage |
| `owner` | Everything `writer` allows, plus manage membership and workspace settings, attach, detach and create catalogs, set a catalog's access mode and grants, publish semantic models, and purge imported lineage |

## Data-grant tiers

In a [scoped catalog](../access/scoped-access.md), a member sees only what a grant gives them. A grant names a catalog,
schema or table and covers everything below it, and its tier is capped at the member's workspace role.

| Tier | Allows |
|---|---|
| `metadata` | See the object and `DESCRIBE` it, but not read its rows |
| `reader` | Everything `metadata` allows, plus read rows: query, sample, and open results |
| `writer` | Everything `reader` allows, plus write and run DDL |

## Agent tiers

Each principal's access to an [agent](../access/agent-access.md) resolves to one tier. On an `open` agent everyone
signed in has `use`; on a `restricted` agent, `use` needs a grant too.

| Tier | Allows |
|---|---|
| `use` | Send queries, SQL sessions and scheduled jobs to the agent; see its status and monitoring page |
| `operate` | Everything `use` allows, plus restart, terminate and force-disconnect it, and revoke its credential |
| `admin` | Everything `operate` allows, plus delete it, change its access mode, and grant or revoke access to it |

A grant on a workspace reaches every member and is capped at `operate`. Holding `agents:manage` gives `admin` on every
agent.

## Related

- [How access works](../access/how-access-works.md) — how the layers combine.
- [Scoped access](../access/scoped-access.md) — grants inside a catalog.
- [Agent access](../access/agent-access.md) — reserving compute.
