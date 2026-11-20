---
type: reference
---

# Access and lineage tables

Every column of the `duckhaven.access` and `duckhaven.lineage` tables: the audit log, current access, accounts, tokens,
which tables each query used, and lineage. The permission each needs, how often it is
refreshed, how long it keeps its rows, and the rules every table follows, are in
[System catalog tables](system-catalog.md).

## access.audit

One row per audit event, with the fields of the [audit event](audit-events.md#event-fields) as columns. An event's
`label` is not copied.

| Column | Type | Event field |
|---|---|---|
| `id` | `UUID` | `id` |
| `event_time` | `TIMESTAMP WITH TIME ZONE` | `event_time` |
| `event_version` | `INTEGER` | `event_version` |
| `category` | `VARCHAR` | `category` |
| `action` | `VARCHAR` | `action` |
| `outcome` | `VARCHAR` | `outcome` |
| `actor_id` | `UUID` | `actor.id` |
| `actor_email` | `VARCHAR` | `actor.email` |
| `actor_type` | `VARCHAR` | `actor.type` |
| `on_behalf_of_id` | `UUID` | `actor.on_behalf_of_id` |
| `on_behalf_of_email` | `VARCHAR` | `actor.on_behalf_of_email` |
| `auth_method` | `VARCHAR` | `actor.auth_method` |
| `credential_id` | `UUID` | `actor.credential_id` |
| `request_id` | `VARCHAR` | `request.id` |
| `source_ip` | `VARCHAR` | `request.source_ip` |
| `user_agent` | `VARCHAR` | `request.user_agent` |
| `workspace_id` | `UUID` | `workspace.id` |
| `workspace_slug` | `VARCHAR` | `workspace.slug` |
| `target_type` | `VARCHAR` | `target.type` |
| `target_id` | `VARCHAR` | `target.id` |
| `target_name` | `VARCHAR` | `target.name` |
| `subject_id` | `UUID` | `subject.id` |
| `subject_email` | `VARCHAR` | `subject.email` |
| `self_change` | `BOOLEAN` | `self_change` |
| `before` | `VARCHAR`, JSON | `before` |
| `after` | `VARCHAR`, JSON | `after` |
| `details` | `VARCHAR`, JSON | `details` |
| `error_code` | `VARCHAR` | `error_code` |

## access.grants

One row per grant. `kind` says what the row grants, and so which columns are set; `snapshot_time` is set on every row.

| `kind` | Grants | `level` | Columns set |
|---|---|---|---|
| `global_role` | A [global role](permissions.md) | `admin` or `user` | `principal_*` |
| `workspace_member` | Membership of a workspace | `owner`, `writer` or `reader` | `principal_*`, `workspace_*` |
| `catalog_attachment` | A catalog to a workspace | `open` or `scoped`, the [access mode](../access/scoped-access.md) | `workspace_*`, `catalog_*`, `granted_by_*`, `granted_at` |
| `catalog_grant` | A [data grant](../access/scoped-access.md#grants-and-tiers) | `metadata`, `reader` or `writer` | `principal_*`, `catalog_*`, `schema_name`, `table_name`, `granted_by_*`, `granted_at` |
| `agent_access_mode` | An agent's [access mode](../access/agent-access.md#open-and-restricted-agents) | `open` or `restricted` | `agent_*` |
| `agent_grant` | An [agent grant](../access/agent-access.md) | `use`, `operate` or `admin` | `principal_*` or, for a grant to a workspace, `principal_type` and `workspace_*`; `agent_*`, `granted_by_*`, `granted_at` |
| `workspace_default_agent` | `use` of a workspace's default compute to its members | `use` | `workspace_*`, `agent_*` |

| Column | Type | Meaning |
|---|---|---|
| `kind` | `VARCHAR` | What the row grants; see above |
| `principal_type` | `VARCHAR` | `user`, `service_account`, or `workspace` for an agent grant to every member of one |
| `principal_id`, `principal_email` | `UUID`, `VARCHAR` | The account granted |
| `principal_active` | `BOOLEAN` | Whether that account can sign in; `false` once deactivated |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace |
| `catalog_id`, `catalog_slug` | `UUID`, `VARCHAR` | The catalog |
| `schema_name`, `table_name` | `VARCHAR` | What a `catalog_grant` covers. Both empty for the whole catalog, `table_name` empty for a whole schema |
| `agent_id`, `agent_name` | `UUID`, `VARCHAR` | The agent |
| `level` | `VARCHAR` | The role, mode or tier; see above |
| `granted_by_id`, `granted_by_email` | `UUID`, `VARCHAR` | Who made the grant, when recorded |
| `granted_at` | `TIMESTAMP WITH TIME ZONE` | When it was made, when recorded |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## access.users

One row per user and service account, deactivated ones included.

| Column | Type | Meaning |
|---|---|---|
| `user_id`, `email`, `name` | `UUID`, `VARCHAR`, `VARCHAR` | The account |
| `principal_type` | `VARCHAR` | `user` or `service_account` |
| `auth_provider` | `VARCHAR` | How it signs in: `local`, `ldap`, `oidc:⟨provider id⟩`, or `service_account` |
| `global_role` | `VARCHAR` | Its [global role](permissions.md): `admin` or `user` |
| `is_active` | `BOOLEAN` | Whether it can sign in; `false` once deactivated |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was created |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## access.workspaces

One row per workspace.

| Column | Type | Meaning |
|---|---|---|
| `workspace_id`, `workspace_slug`, `name` | `UUID`, `VARCHAR`, `VARCHAR` | The workspace |
| `description` | `VARCHAR` | Its description |
| `timezone` | `VARCHAR` | The time zone its schedules run in |
| `default_agent_id`, `default_agent_name` | `UUID`, `VARCHAR` | Its [default compute](../access/workspaces.md#default-compute); empty when it has none |
| `result_cache_enabled` | `BOOLEAN` | Whether the [result cache](../querying/result-cache.md) answers its queries |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was created |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## access.credentials

One row per [personal access token](rest-api.md#your-own-tokens), of a user or a service account. The token, and its
hash, are not copied. The [assistant](../ai/assistant.md)'s tokens, which last for one answer, are left out.

| Column | Type | Meaning |
|---|---|---|
| `credential_id` | `UUID` | The token's id, as in `credential_id` of `access.audit` |
| `kind` | `VARCHAR` | `pat` |
| `principal_type` | `VARCHAR` | `user` or `service_account` |
| `principal_id`, `principal_email` | `UUID`, `VARCHAR` | Who holds it |
| `principal_active` | `BOOLEAN` | Whether its holder can sign in |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | When it was issued |
| `expires_at` | `TIMESTAMP WITH TIME ZONE` | When it stops working; empty for a token that never expires |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## access.table_access

One row per table, and kind of access, of each `done` query in `query.history`, worked out from its SQL. A query that
reads and writes the same table has two rows.

- **Names resolve as DuckDB binds them.** An unqualified name is in the catalog and schema the query ran in; a two-part
  `x.orders` is in catalog `x`'s `main` schema when `x` is a catalog, and otherwise in schema `x`.
- **Names are in lower case**, as DuckDB matches them.
- **Left out:** a table in no DuckHaven catalog (a temporary table, or this catalog), a `DESCRIBE`, and a query whose
  SQL DuckHaven cannot parse. A view counts as the view, not the tables it reads.

| Column | Type | Meaning |
|---|---|---|
| `query_id` | `UUID` | The query, as `id` in `query.history` |
| `catalog_id`, `catalog_name` | `UUID`, `VARCHAR` | The table's catalog |
| `schema_name`, `table_name` | `VARCHAR` | The table |
| `access` | `VARCHAR` | `read` or `write` |
| `finished_at` | `TIMESTAMP WITH TIME ZONE` | When the query finished |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | The workspace it ran in |
| `user_id`, `user_email` | `UUID`, `VARCHAR` | Who ran it |
| `on_behalf_of_id`, `on_behalf_of_email` | `UUID`, `VARCHAR` | The person a delegated run acted for |

## lineage.table_lineage

One row per relationship in the [lineage graph](../metadata/lineage.md), across every catalog, with every table named:
unlike the graph in a workspace, nothing is hidden for lack of a grant.

| Column | Type | Meaning |
|---|---|---|
| `edge_id` | `UUID` | The relationship |
| `provider` | `VARCHAR` | What asserted it: `execution` for SQL DuckHaven ran, or an importer such as `dbt` |
| `operation` | `VARCHAR` | The statement that produced it, such as `insert` or `create_table_as` |
| `confidence` | `VARCHAR` | `exact` |
| `column_lineage` | `VARCHAR` | Whether its column detail is known: `derived`, `unknown` or `unsupported`; see [Column-level lineage](../metadata/lineage.md#column-level-lineage) |
| `source_catalog_id`, `source_catalog_name` | `UUID`, `VARCHAR` | The source's catalog; empty for a source outside DuckHaven |
| `source_system` | `VARCHAR` | For a source outside DuckHaven, the system its producer named |
| `source_schema`, `source_table` | `VARCHAR` | The source |
| `target_catalog_id`, `target_catalog_name`, `target_system`, `target_schema`, `target_table` | as for the source | The target |
| `workspace_id`, `workspace_slug` | `UUID`, `VARCHAR` | Where it was observed |
| `last_query_id` | `UUID` | The query that last produced it |
| `first_seen_at`, `last_seen_at` | `TIMESTAMP WITH TIME ZONE` | When it was first and last observed |
| `observation_count` | `INTEGER` | How many times it has been observed |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## lineage.column_lineage

One row per column-to-column relationship, under a relationship of `table_lineage`.

| Column | Type | Meaning |
|---|---|---|
| `id` | `UUID` | The column relationship |
| `edge_id` | `UUID` | Its table relationship, as in `table_lineage` |
| `source_catalog_id`, `source_catalog_name`, `source_system`, `source_schema`, `source_table` | as in `table_lineage` | The source table |
| `source_column` | `VARCHAR` | The source column |
| `target_catalog_id`, `target_catalog_name`, `target_system`, `target_schema`, `target_table` | as in `table_lineage` | The target table |
| `target_column` | `VARCHAR` | The target column |
| `first_seen_at`, `last_seen_at` | `TIMESTAMP WITH TIME ZONE` | When it was first and last observed |
| `snapshot_time` | `TIMESTAMP WITH TIME ZONE` | When the table was last replaced |

## Related

- [System catalog tables](system-catalog.md): every table, the permission it needs, and how fresh it is.
- [Audit events](audit-events.md): every action, and what each records in `before`, `after` and `details`.
- [Review access changes](../access/review-access-changes.md): reading the audit log and current access.
