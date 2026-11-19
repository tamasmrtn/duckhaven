---
type: reference
---

# Audit events

Every action the [access audit log](../access/audit-log.md) records, the fields each event carries, and what each
action puts in `before`, `after` and `details`.

Action names never change once released, and new ones are only added, so a saved filter or a SIEM rule keeps working
across upgrades. Fields are likewise only added: `event_version` changes if an existing field ever changes meaning.

## Event fields

An event as `GET /api/admin/audit/events` returns it. The [log line](../access/audit-log.md#stream-events-to-a-siem)
carries the same fields except `label`.

| Field | Meaning |
|---|---|
| `id` | The event's UUID |
| `event_time` | When the change committed, in UTC |
| `event_version` | The shape of this event, `1` |
| `category` | `identity`, `access`, `credential`, `auth` or `authz`; see [Actions](#actions) |
| `action` | What happened; see [Actions](#actions) |
| `label` | A short description of the action, for display |
| `outcome` | `success`; `failure` for a refused sign-in; `denied` for a request refused for lack of access |
| `actor.id`, `actor.email` | Who acted. Empty for the system and for a caller who is not signed in |
| `actor.type` | `user`, `service_account`, `system` (DuckHaven itself), `idp` (an identity provider, at sign-in) or `anonymous` |
| `actor.on_behalf_of_id`, `actor.on_behalf_of_email` | The person a delegated call acted for, such as the person the [assistant](../ai/assistant.md) answered |
| `actor.auth_method` | `session` (the web app), `pat` (a token) or `system` |
| `actor.credential_id` | The session or token the request presented. Never its secret |
| `request.id` | The request's id, also sent back to the caller as the `X-Request-ID` response header |
| `request.source_ip`, `request.user_agent` | Where the request came from, as the API saw it behind your [reverse proxy](../deployment/reverse-proxy-tls.md) |
| `workspace.id`, `workspace.slug` | The workspace the event belongs to, if any |
| `target.type`, `target.id`, `target.name` | What was changed: a user, a grant, a catalog, an agent, a route |
| `subject.id`, `subject.email` | Whose access changed, when that is a person or service account |
| `self_change` | `true` when the subject is the actor, or the person a delegated call acted for |
| `before`, `after` | The changed values; see [What each action records](#what-each-action-records) |
| `details` | Context that is not a changed value; see [What each action records](#what-each-action-records) |
| `error_code` | For a refusal, the error code the caller received; see [Errors](errors.md) |

Names (`actor.email`, `workspace.slug`, `target.name`, `subject.email`) are kept as they were when the event happened,
so an event about a deleted user, workspace or catalog still reads in full.

## What each action records

| Actions | `before` / `after` | `details` |
|---|---|---|
| `user.create`, `service_account.create`, `setup.first_admin` | `role` after | `authorized_by: setup_token` for the first admin |
| `user.provision` | `role` after | `provider`, the account's sign-in provider |
| `user.role_change`, `service_account.role_change` | `role` | `provider` when an identity provider changed it at sign-in; `reason: last_active_admin` on a `denied` sign-in change that would have demoted the last active admin |
| `user.deactivate`, `user.reactivate`, `service_account.deactivate`, `service_account.reactivate` | `is_active` | – |
| `user.sessions_revoke` | – | `sessions_revoked`, the number of sessions ended |
| `service_account.delete` | `role` and `is_active` before | `memberships_removed`, `grants_removed` |
| `workspace.create` | – | – |
| `workspace.delete` | – | `members_removed`, `catalogs_detached` |
| `workspace.default_agent_change` | `default_agent_id` | – |
| `workspace_member.add`, `workspace_member.role_change`, `workspace_member.remove` | `role` | `reason: workspace_creator` for the owner a new workspace starts with |
| `catalog.create` | – | `kind`, `storage_backend_id`, `storage_kind`; `export_id` and `source_catalog` for an export's copy |
| `catalog.attach` | `access_mode` after | `default`, whether it is the workspace's default catalog |
| `catalog.detach` | `access_mode` before | – |
| `catalog.drop` | – | `kind` |
| `catalog.access_mode_change`, `agent.access_mode_change` | `access_mode` | – |
| `catalog.export` | – | `export_id`, `target_name`, `target_storage_backend_id` |
| `catalog_grant.create`, `catalog_grant.tier_change`, `catalog_grant.delete` | `tier` | `catalog_id`, `catalog`, `schema`, `table`; `reason` (below) |
| `agent_grant.create`, `agent_grant.tier_change`, `agent_grant.delete` | `tier` | `agent_id`; `principal`, `user` or `workspace` |
| `pat.issue` | `expires_at` after | – |
| `pat.revoke` | `created_at` and `expires_at` before | – |
| `catalog.ducklake_role_rotate` | – | – |
| `catalog.ducklake_roles_reconcile` | – | `catalogs`, `reconciled` |
| `auth.sign_in` | – | `method` (`password`, `oidc` or `setup_token`), `provider` |
| `auth.sign_in_failed` | – | `method`; for single sign-on, `provider` and `reason` (`callback_failed`, `missing_identity_claims` or `refused`) |
| `auth.sign_out` | – | – |
| `auth.rate_limited` | – | `method` |
| `authz.permission_denied` | – | `permission`, the global permission the caller lacked |
| `authz.grant_denied` | – | `reason`, as the caller saw it, and `statement`, the first 2,048 characters of the SQL |

A data grant's `reason` says why it was created or removed: `revoked` (removed by hand), `object_dropped` (its schema
or table was dropped), `catalog_dropped`, or `creator_of_scoped_catalog` (the `writer` grant the creator of a scoped
catalog receives).

For a sign-in that failed, `target.name` is the identifier that was submitted, cut to 255 characters. It is recorded
whether or not it names an account, and the event looks the same either way.

## Actions

| Action | Category | Target | Records |
|---|---|---|---|
| `agent.access_mode_change` | `access` | `agent` | Changed an agent's access mode |
| `agent_grant.create` | `access` | `agent_grant` | Granted access to an agent |
| `agent_grant.delete` | `access` | `agent_grant` | Removed an agent grant |
| `agent_grant.tier_change` | `access` | `agent_grant` | Changed an agent grant's tier |
| `catalog.access_mode_change` | `access` | `catalog` | Changed a catalog's access mode |
| `catalog.attach` | `access` | `catalog` | Attached a catalog to a workspace |
| `catalog.create` | `access` | `catalog` | Created a catalog |
| `catalog.detach` | `access` | `catalog` | Detached a catalog from a workspace |
| `catalog.drop` | `access` | `catalog` | Dropped a catalog |
| `catalog.export` | `access` | `catalog` | Exported a catalog to a new catalog |
| `catalog_grant.create` | `access` | `catalog_grant` | Granted access to data |
| `catalog_grant.delete` | `access` | `catalog_grant` | Removed a data grant |
| `catalog_grant.tier_change` | `access` | `catalog_grant` | Changed a data grant's tier |
| `workspace.create` | `access` | `workspace` | Created a workspace |
| `workspace.default_agent_change` | `access` | `workspace` | Changed a workspace's default compute |
| `workspace.delete` | `access` | `workspace` | Deleted a workspace |
| `workspace_member.add` | `access` | `workspace_member` | Added a workspace member |
| `workspace_member.remove` | `access` | `workspace_member` | Removed a workspace member |
| `workspace_member.role_change` | `access` | `workspace_member` | Changed a workspace member's role |
| `auth.rate_limited` | `auth` | `user` | Was refused sign-in for too many attempts |
| `auth.sign_in` | `auth` | `user` | Signed in |
| `auth.sign_in_failed` | `auth` | `user` | Failed to sign in |
| `auth.sign_out` | `auth` | `user` | Signed out |
| `authz.grant_denied` | `authz` | `route` | Was refused by a data grant check |
| `authz.permission_denied` | `authz` | `route` | Was refused for lacking a global permission |
| `catalog.ducklake_role_rotate` | `credential` | `catalog` | Rotated a DuckLake catalog's database password |
| `catalog.ducklake_roles_reconcile` | `credential` | `catalog` | Reconciled every DuckLake catalog's database role |
| `pat.issue` | `credential` | `pat` | Issued a personal access token |
| `pat.revoke` | `credential` | `pat` | Revoked a personal access token |
| `service_account.create` | `identity` | `user` | Created a service account |
| `service_account.deactivate` | `identity` | `user` | Deactivated a service account |
| `service_account.delete` | `identity` | `user` | Deleted a service account |
| `service_account.reactivate` | `identity` | `user` | Reactivated a service account |
| `service_account.role_change` | `identity` | `user` | Changed a service account's role |
| `setup.first_admin` | `identity` | `user` | Created the first admin during setup |
| `user.create` | `identity` | `user` | Created a user |
| `user.deactivate` | `identity` | `user` | Deactivated a user |
| `user.provision` | `identity` | `user` | Created a user at their first sign-in |
| `user.reactivate` | `identity` | `user` | Reactivated a user |
| `user.role_change` | `identity` | `user` | Changed a user's global role |
| `user.sessions_revoke` | `identity` | `user` | Signed a user out everywhere |

## Related

- [Access audit log](../access/audit-log.md) — what is recorded, when, and for how long.
- [Review access changes](../access/review-access-changes.md) — filtering the log.
- [Permissions](permissions.md) — `audit:read`, which reading the log needs.
