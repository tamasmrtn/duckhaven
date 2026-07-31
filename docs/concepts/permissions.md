# Identity & permissions

DuckHaven separates **who you are** (identity) from **what you can do** (authorization). Authorization is always
enforced at the API boundary before any query reaches an [agent](agents.md) — DuckHaven is the sole permission
authority.

## Identity (how you sign in)

An account authenticates one of three ways, recorded on the user as its `auth_provider`:

- **Local** — email and a bcrypt-hashed password. Always available, so a break-glass admin can sign in even when an
  external IdP is unreachable.
- **OIDC SSO** — the organization's identity provider (Okta, Microsoft Entra ID, Google Workspace, Keycloak,
  Authentik, …) via the standard Authorization Code + PKCE flow. See [Connect an IdP (SSO)](../guides/connect-idp.md).
- **LDAP / Active Directory** — a directory bind, used as a secondary path. See
  [Connect LDAP / AD](../guides/connect-ldap.md).

Local accounts always coexist with SSO/LDAP. When you submit a password, DuckHaven verifies it against a **local**
account first (the break-glass path); only if there is no local password for that email does it try an LDAP bind. OIDC
is a separate "Sign in with SSO" button. A session is the same in every case: an opaque, server-stored cookie
(`session`), so logout and expiry behave identically regardless of how you signed in.

### Just-in-time provisioning

The first time someone signs in through SSO or LDAP, DuckHaven creates their account automatically (matched by email)
— no manual pre-provisioning. On each subsequent sign-in their name and [role](#global-roles-permissions) are
re-synced from the IdP, so the directory stays authoritative. An email already registered to a *different* provider is
refused, which prevents a federated identity from taking over the local admin.

## Global roles & permissions

Every user has one **global role** backing a set of enumerated **permissions**. DuckHaven ships two built-in roles:

| Role | Permissions | Meaning |
|---|---|---|
| `admin` | all | Manage agents, storage backends, users, and maintenance; administer any catalog and the full query log. |
| `user` | none (global) | A normal account. Workspace access is granted separately (below). |

Permissions are checked individually at each admin endpoint (for example `users:manage` gates the user-management
APIs), so the model reads cleanly in a security review rather than hiding behind a single "is admin" flag.

### Mapping IdP groups to roles

For SSO/LDAP users, the global role is derived from the IdP's group claims on every sign-in via a configured mapping
(for example `dh-admins → admin`). This makes onboarding and offboarding a directory operation: add someone to the
admin group in your IdP and they become an admin on next login; remove them and they are demoted. Group membership maps
to the **global role only** — workspace membership remains an explicit DuckHaven operation.

## Workspace roles

Independently of the global role, each member of a [workspace](workspaces.md) has one role:

| Role | Can do |
|---|---|
| `reader` | List and browse schemas and tables; run queries; view history |
| `writer` | Everything a reader can, plus create/modify/drop tables and run DDL |
| `owner` | Everything a writer can, plus manage workspace membership |

By default this role applies uniformly to every schema and table in every catalog
the workspace attaches. To scope access more finely, see **scoped access** below.

## Scoped access

A workspace role is coarse: it grants the same access to *every* object in *every*
attached catalog. Sometimes you want less — "this analyst sees the `marketing`
schema but not `finance`," or "this coding-agent service account can *discover* a
table but never read its rows." **Scoped access** provides that, opt-in per catalog
attachment, without changing anything for teams that don't need it.

Each catalog attachment has an **access mode**:

- **`open`** (the default) — the workspace role governs every object. Today's
  behavior, unchanged.
- **`scoped`** — the workspace role is no longer enough on its own; a member sees
  only what a **grant** gives them on that catalog.

Pick the mode when you create the catalog — the **New catalog** dialog asks who can see its data —
or switch an existing one from its **Permissions** dialog in the catalog view (right-click the
catalog), or from **Admin → Catalog access**, which lists every attached catalog with its mode.

Creating it scoped matters when the data is sensitive from the start: a catalog created `open` is
readable by every workspace member at their workspace role from the moment it exists, so switching
it afterwards leaves a window. A catalog created scoped grants its creator `writer` on the whole
catalog, because scoped access has **no** bypass — the workspace role only caps a grant, it never
supplies one, so without that seed the catalog would be invisible even to the person who made it.

A grant is set at one of three levels of the hierarchy, and a grant at a coarser
level covers everything beneath it — including tables created *after* the grant:

- **catalog** — every schema and table in the catalog
- **schema** — every current and future table in that schema
- **table** — one table

Each grant carries a **tier**, extending the role vocabulary with a discovery-only
level:

| Tier | Can do |
|---|---|
| `metadata` | Discover and describe the object (list it, `DESCRIBE` it to read its columns) — but **not** read its rows |
| `reader` | Everything `metadata` can, plus read rows (query / sample) |
| `writer` | Everything `reader` can, plus write and run DDL |

Two rules keep resolution predictable, matching Databricks Unity Catalog and
Snowflake:

- **Grants only narrow, never widen.** A principal's effective tier is capped at
  their workspace role — a schema-level `writer` grant cannot promote a workspace
  `reader` past what `reader` allows. To restrict someone to a subset of a catalog,
  don't grant broadly — grant narrowly.
- **Grants are additive, with no deny.** Where several grants apply, the highest
  wins; there is no override/deny mechanism.

Enforcement happens at the same API boundary as workspace roles — for both the REST
browsing endpoints and raw SQL (interactive *and* scheduled). A query that joins
several tables is rejected before it runs if the principal lacks at least `reader`
on **any** referenced table. Denied objects return a 404 (not a 403) at the leaf, so
a restricted table is indistinguishable from one that does not exist. Grants apply
equally to human members and [service accounts](../guides/service-accounts.md), and
are managed from the **catalog view** — right-click a catalog, schema, or table (or
open a table's **Permissions** tab) to grant a principal access at that level, the
same object-first workflow as Databricks Unity Catalog's Catalog Explorer.

### Discovering objects in a scoped catalog

In a scoped catalog, **browse the catalog tree (or the REST catalog endpoints) to find out what exists, and use
`DESCRIBE <catalog>.<schema>.<table>` for a table's columns.** Both are filtered by your grants: you see the schemas
and tables you were granted and nothing else, and a `DESCRIBE` needs only the `metadata` tier on its table.

What you cannot do in a scoped catalog is ask the query engine to *enumerate* objects for you. These are rejected:

- `information_schema.tables`, `information_schema.schemata`, `information_schema.columns` (and the
  `system.information_schema.…` spelling of the same views)
- the `duckdb_tables()`, `duckdb_schemas()`, `duckdb_columns()`, `duckdb_views()` family
- `SHOW TABLES` / `SHOW ALL TABLES`, `PRAGMA show_tables`, `PRAGMA database_list`
- `PRAGMA table_info(…)` — it names its table only as a text string, which DuckHaven cannot resolve to an object to
  check a grant against; `DESCRIBE` is the equivalent that can be checked

The reason is mechanical: DuckDB computes these across *every* catalog attached to the worksheet and offers no way to
filter their rows, so allowing them would reveal the names of tables you have not been granted — the very thing scoped
access exists to prevent. Rejecting them is a deliberate trade: the engine cannot filter, so the answer must come from
the API, which can.

#### The rejection is workspace-wide, not per catalog

That same mechanic has a consequence worth knowing before you scope anything. A worksheet does not attach only the
catalog you are working in — it attaches **every** catalog the workspace binds, and
[these views span all of them](../reference/sql-support.md#inspecting-metadata-information_schema) at once. There is
therefore no such thing as enumerating "just the open catalog": the rows come back for the scoped one too.

So the moment **one** catalog in a workspace is switched to `scoped`, engine-side enumeration is rejected for **every**
session in that workspace, whichever catalog is active. A worksheet pointed at an entirely open catalog will still be
refused `information_schema.tables`. This is deliberate — the alternative leaks the scoped catalog's object names — and
the error message names the scoped catalog responsible, so a denial from an open catalog is explicable.

Nothing else changes for the open catalogs: querying their rows, `DESCRIBE`, and the catalog tree all keep working
exactly as before. Only the unfilterable listings go away.

!!! tip "Give scoped catalogs their own workspace"
    If tools in a workspace rely on `information_schema` — dbt and most BI connectors do — attach the scoped catalog to
    a **separate** workspace rather than alongside them. Scoping in place is not a local change; it withdraws
    enumeration from everyone in the workspace.

!!! note "Tools that enumerate"
    A tool that discovers relations through `information_schema` (dbt does, for instance) will fail in any workspace
    holding a scoped catalog. Have it list objects through the catalog API, or point it at a workspace with no scoped
    catalogs. Note that column metadata is
    unaffected either way: `information_schema.columns`
    [does not work for Iceberg tables](../reference/sql-support.md#columns-and-types-use-describe) regardless of access
    mode, so any tool that works against DuckHaven at all is already using `DESCRIBE` for that.

## Per-agent access

Scoped access answers "which *data* may this person reach". Per-agent access answers a different question: "which
*compute* may they run it on". As the [elastic fleet](elastic-compute.md) is shared, an agent stops being an
interchangeable worker and becomes something with its own cost, blast radius, and data proximity — one that a team may
reasonably own, and that another team should perhaps not be able to restart.

Until this existed, agents were all-or-nothing in both directions: the single `agents:manage` permission governed every
agent in the deployment, while *using* an agent was not restricted at all — any signed-in user could target any agent
for a query, a SQL session, or a schedule.

### The three tiers

Each principal's access to each agent resolves to one of three tiers, each including everything below it:

| Tier | Can do |
|---|---|
| `use` | Target the agent for queries, SQL sessions, and scheduled jobs; see its status and its monitoring page |
| `operate` | Everything in `use`, plus restart, terminate, force disconnect, and revoke its credential |
| `admin` | Everything in `operate`, plus delete the agent, change its access mode, and grant or revoke access to it |

Holding `agents:manage` globally still confers `admin` on **every** agent, automatically. Per-agent access is an
overlay on the global model, never a replacement — it can add access for people who hold no global agent permission,
and it can never take access away from someone who does.

Two things stay deliberately **fleet-level**, on `agents:manage` alone: creating an agent and minting a bootstrap
token. Both are spending decisions about the deployment, and neither has an agent yet to hold a tier on.

### Open and restricted agents

Each agent has an **access mode**, mirroring how a catalog attachment is `open` or `scoped`:

- **`open`** (the default) — any signed-in user may use the agent. This is exactly how every agent behaved before
  per-agent access existed, so nothing changes for a deployment that never opts in.
- **`restricted`** — using the agent requires an explicit grant. To anyone without one the agent is *invisible*: it
  does not appear in the engine picker or the agent list, and its pages report "not found" rather than "forbidden".

Only the `use` tier is affected by the mode. `operate` and `admin` always require a grant (or `agents:manage`), on an
open agent just as much as on a restricted one.

!!! note "Open mode is a floor, not a ceiling"
    On an open agent a grant can still *raise* someone — to `operate`, say, so they can restart it — but it can never
    lower them below `use`. Grants are additive and there is no negative grant, the same rule scoped access follows.

### Granting to a person or to a workspace

A grant names either a **user** (a person or a service account) or a **workspace**. A workspace grant reaches every
member of that workspace and follows membership automatically: add someone to the workspace and they gain the agent,
remove them and they lose it, with no ACL edit either way. A user's effective tier is the highest of their own grant
and the grants on every workspace they belong to.

Both exist because they answer different questions. "Everyone in Analytics may run work on the shared ADLS agent" is
unmanageable one person at a time, and stale the moment the team changes. "Dana may restart it" is a personal
responsibility that should name Dana.

A workspace grant is capped at `operate`. The `admin` tier includes granting access, and delegating that to *whoever
happens to be in a workspace* would make the access list unauditable — the set of people who can widen it would change
silently every time someone joined.

Manage this from **Compute → (an agent) → Access**.

## What is not in scope

- **No row- or column-level security.** Object-level grants reach down to the
  catalog, schema, and table (see [Scoped access](#scoped-access)),
  but not to individual rows or columns — the same boundary Unity Catalog and
  Snowflake draw between object grants and row filters / column masks.
- **No group-based *data* grants.** A catalog grant targets a principal (member or
  service account) directly; there is no grantable group concept for data access.
  [Per-agent access](#per-agent-access) is the one exception — it can name a
  workspace as well as a user — and that does not extend to catalogs, schemas, or tables.
- **No SCIM.** Provisioning is just-in-time at login, not a push from the directory.
- **RBAC is API-enforced only.** Roles and workspace membership are enforced by DuckHaven; Polaris sees only the API
  service principal and is never granted per-user access. The catalog grant mirror is intentionally a no-op.

## The AI assistant is governed the same way

The [AI data assistant](assistant.md) is not a special principal. It acts as a service account, and its data access is
governed entirely by that account's workspace membership and catalog grants — including the `metadata` tier for a
browse-only assistant. Its tools call the same REST endpoints described here, as that account, so scoped grants and the
SQL guard apply unchanged. Nothing about the assistant widens access; grants remain the single source of truth.

## Related

- [Manage users & access](../guides/users-access.md) — create users, assign roles, offboard.
- [Connect an IdP (SSO)](../guides/connect-idp.md) and [Connect LDAP / AD](../guides/connect-ldap.md).
- [Offboarding & break-glass](../operations/offboarding.md) — revoke access and recover from IdP outages.
