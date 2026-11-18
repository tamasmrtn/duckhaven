---
type: concept
---

# Access audit log

DuckHaven records every change to who can reach what, every sign-in, and every request refused for lack of access, in
an append-only log kept for a year.

The log answers the questions query history cannot: who made Ana an owner of `analytics`, who revoked a grant and
when, whether an admin added themselves to a workspace to read its data. Each event names who acted, from where, whose
access changed, and the values before and after.

!!! info "Availability"
    On in every deployment, with no setting to turn it off. Reading it needs the `audit:read` permission, which the
    `admin` role holds, and it is read in the web app at **Admin** → **Audit** or through the REST API at
    `GET /api/admin/audit/events`.

## What is recorded

| Category | What it covers |
|---|---|
| `identity` | Accounts and their global roles: a user or service account created, given another role, deactivated, reactivated or deleted; a user signed out everywhere; the first admin created at setup; an account created, or its role changed, by your identity provider at sign-in |
| `access` | Who can reach which workspace, catalog, table and agent: workspaces created and deleted, and a workspace's default compute; members added, given another role and removed; catalogs created, attached, detached, dropped and exported, and their access mode; data grants and agent grants created, changed and removed |
| `credential` | Personal access tokens issued and revoked, for people and service accounts; DuckLake catalog roles rotated and reconciled |
| `auth` | Sign-ins, by password, LDAP, single sign-on or the setup token; refused sign-ins; sign-ins refused for too many attempts; sign-outs |
| `authz` | Requests refused because the caller lacked a global permission, and statements refused by a data grant |

Every action, and what each one records, is listed in the [audit events reference](../reference/audit-events.md).

The log does not record:

- **Work inside a workspace.** Queries, worksheets, dashboards, schedules and saved queries are in History and the
  [session audit trail](../querying/session-audit.md), not here.
- **Configuration that grants no access.** Storage backends, the maintenance policy, and agent lifecycle (creating,
  restarting and deleting compute, and its bootstrap credentials) are not access changes.
- **An agent's access mode when it is created.** Changing it later is recorded.
- **Other refusals.** A request refused by a workspace role or an agent tier, or answered `404` because the caller
  cannot see the object, is not recorded; only a missing global permission or data grant is.

## How an event reads

The **actor** is the account that made the request. A call the [AI assistant](../ai/assistant.md) made for a person
names both: the assistant's service account as the actor and the person under `actor.on_behalf_of_email`. An identity
provider changing a role at sign-in has the actor type `idp`, and a background task, such as a schedule firing, has
the type `system` or names the account the task ran as.

The **subject** is whose access changed. When the subject is the actor, or the person a delegated call acted for, the
event is marked `self_change`. That is the mark to watch: an admin can make themselves a member of any workspace, and
every such change is recorded with it.

On the **Audit** page the actor is under **By**, with the person a delegated call acted for on the line below it, the
subject is under **Affecting**, and a `self_change` event carries an **Own access** badge.

Names are kept as they were when the event happened. An event about a user, workspace or catalog that has since been
deleted still reads in full.

Every response from the API carries an `X-Request-ID` header, and the events a request recorded carry the same id, so
a client's log line can be matched to the change it made.

## When an event is written

A change and its event are written in the same database transaction: if the change is saved, so is its event, and if
it fails or is rolled back, neither is. A request that changes nothing, such as giving someone the role they already
have, records nothing.

A refused sign-in or a refused request fails, so its event is written on its own. The same refusal repeated by the
same caller with the same details within a minute is recorded once, so a flood of failed requests cannot flood the
log.

## Retention and integrity

Events are kept for `AUDIT_RETENTION_DAYS`, 365 days by default, and a daily purge deletes older ones. Set it to `0`
to keep every event; see the [configuration reference](../reference/configuration.md#audit-log).

The log lives in the `duckhaven` Postgres database, so [backups](../deployment/backup-restore.md) include it. Nothing
in DuckHaven can change or delete an event except the purge: the table refuses `UPDATE`, `DELETE` and `TRUNCATE`
outside it.

A read-only copy, purged on the same schedule, is kept in the
[system catalog database](../deployment/system-catalog.md), normally within 10 seconds of each event. Admins query it in
SQL as `duckhaven.access.audit`; see [System catalog](../querying/system-catalog.md).

!!! warning "Append-only protects against the application, not against a database administrator"
    Anyone who can connect to Postgres as the database owner can disable that protection. For evidence that survives
    a compromised database, stream the events to a system outside DuckHaven, as below.

## Stream events to a SIEM

Each event is also written, once it is saved, as one log line from the logger `duckhaven.audit` at level `INFO`. The
message is the event as JSON, with the same fields the REST API returns except `label`:

```text
2026-11-17 18:35:34,933 INFO duckhaven.audit [trace_id=- span_id=-] {"id":"b50883b8-bf7c-43a5-a564-3a1175dc7ba0",
"event_time":"2026-11-17T18:35:34.933119+00:00","event_version":1,"category":"access",
"action":"workspace_member.add","outcome":"success","actor":{"email":"admin@example.com",…},…}
```

The line is shown wrapped here; it is one line in the log. Collect the API's standard output with your log shipper
and keep the lines from `duckhaven.audit`. With `LOG_LEVEL` above `INFO` the lines are not written, while the events
are still saved.

## Related

- [Review access changes](review-access-changes.md) — find a change in the web app or with the REST API.
- [Audit events](../reference/audit-events.md) — every action and field.
- [How access works](how-access-works.md) — the access the log records changes to.
- [Offboarding & break-glass](offboarding.md) — removing access, and reviewing the break-glass admin's use.
