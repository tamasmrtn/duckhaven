---
type: how-to
---

# Review access changes

Find who changed someone's access, and what they changed, by filtering the access audit log on the **Audit** page,
through the REST API, or in SQL.

## Before you begin

- **You need:** the `audit:read` permission, which the `admin` role holds. For the REST API, also a token for it: your
  own from `dh auth login`, or a [service account](service-accounts.md)'s with the `admin` role. For SQL, also
  `queries:admin`, which the `admin` role holds too.
- **Applies to:** every deployment. The log is always on; [Access audit log](audit-log.md) explains what it records.

## In the web app

1. Open **Admin** → **Audit**.

    The events of the last 30 days are listed newest first: when each happened, the action, who acted (**By**), what
    it changed (**Target**, with its workspace below), and whose access changed (**Affecting**). The action carries
    its marks: **Failed** or **Denied** for a refusal, and **Own access** for a change someone made to their own
    access.

2. Narrow the list with the filters above it. They combine:

    | Filter | Shows |
    |---|---|
    | **Last 30 days** | Another window, from **Last hour** to **All time**, or a **Custom range** with a start and an end |
    | **All events** | One category: **Accounts**, **Access**, **Tokens**, **Sign-ins** or **Refusals** |
    | **Any outcome** | **Succeeded**, **Failed** (a refused sign-in) or **Denied** (a request refused for lack of access) |
    | **By** | What one person or service account changed, including the calls the [assistant](../ai/assistant.md) made for them |
    | **Affecting** | Every change to one person's or service account's access, by anyone |
    | **Workspace** | One workspace, by its slug |
    | **Changes to own access** | Only changes people made to their own access; each is marked **Own access** |

3. Select an event to open it.

    **Change** lists each value before and after, **Details** what else was recorded, and **Request** the request ID,
    the source IP and how the actor signed in.

The filters are kept in the page's address, so a filtered view can be bookmarked, or sent to someone else who holds
`audit:read`. More events than fit on the page load with **Load more**.

## With the REST API

The examples read the server address and the token from two variables:

```bash
export DH=https://duckhaven.example.com
export DH_TOKEN=dh_pat_…
```

### List the latest access changes

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" \
  "$DH/api/admin/audit/events?category=access&limit=20"
```

Events come newest first. Each one says who acted, whose access changed and how (some fields omitted):

```json
{
  "items": [
    {
      "event_time": "2026-11-17T18:35:34.933119Z",
      "category": "access",
      "action": "workspace_member.add",
      "label": "Added a workspace member",
      "outcome": "success",
      "actor": {"email": "admin@example.com", "type": "user", "on_behalf_of_email": null, "auth_method": "session"},
      "request": {"id": "9f443aecee444316bd0c992424215f6d", "source_ip": "10.0.4.17"},
      "workspace": {"slug": "analytics"},
      "target": {"type": "workspace_member", "name": "ana@example.com"},
      "subject": {"email": "ana@example.com"},
      "self_change": false,
      "before": null,
      "after": {"role": "reader"}
    }
  ],
  "cursor": "M2RiNDFhYzAtYTUyYy00ODhiLWExNzAtNTA5MjU2OGQ0NDNh",
  "has_more": true
}
```

`has_more` is `true` when there are older events. Fetch them by sending `cursor` back with the same filters:

```bash
curl -s -G -H "Authorization: Bearer $DH_TOKEN" "$DH/api/admin/audit/events" \
  -d category=access -d limit=20 -d cursor=M2RiNDFhYzAtYTUyYy00ODhiLWExNzAtNTA5MjU2OGQ0NDNh
```

### Find what changed one person's access

Look up the person's id, then ask for the events whose subject they are:

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" "$DH/api/admin/users?limit=1000" \
  | jq -r '.items[] | select(.email == "ana@example.com") | .id'

curl -s -H "Authorization: Bearer $DH_TOKEN" \
  "$DH/api/admin/audit/events?subject_id=⟨user id⟩"
```

The second call lists every role, membership, grant and token change made to Ana's account, by anyone.

To see what Ana changed instead, pass `actor_id`. It also matches the calls the [assistant](../ai/assistant.md) made
for her, which name its service account as the actor and Ana as `actor.on_behalf_of_email`.

### Find people who changed their own access

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" \
  "$DH/api/admin/audit/events?self_change=true"
```

Each event is one where the actor was also the subject: an admin adding themselves to a workspace, raising their own
role, or issuing themselves a token.

### Narrow by time, workspace or action

Filters combine. This lists the data grants created, changed or removed in `analytics` during November 2026:

```bash
curl -s -G -H "Authorization: Bearer $DH_TOKEN" "$DH/api/admin/audit/events" \
  -d workspace=analytics -d 'action=catalog_grant.*' \
  -d since=2026-11-01T00:00:00Z -d until=2026-12-01T00:00:00Z
```

| Parameter | Matches |
|---|---|
| `since`, `until` | Events at or after `since`, and before `until`, as ISO 8601 times |
| `category` | `identity`, `access`, `credential`, `auth` or `authz` |
| `action` | One action, such as `catalog_grant.delete`, or every action with a prefix, such as `catalog_grant.*` |
| `actor_id` | Events this account made, or that were made on its behalf |
| `subject_id` | Events that changed this account's access |
| `workspace` | A workspace slug or id |
| `target_type`, `target_id` | What was changed, such as `catalog_grant` and a grant's id |
| `outcome` | `success`, `failure` (a refused sign-in) or `denied` (a request refused for lack of access) |
| `self_change` | `true` for changes the actor made to their own access |
| `limit`, `cursor` | Page size, at most 1,000, and the page to continue from |

Every action, and the fields it records, is in the [audit events reference](../reference/audit-events.md).

### Find refused sign-ins

```bash
curl -s -H "Authorization: Bearer $DH_TOKEN" \
  "$DH/api/admin/audit/events?action=auth.sign_in_failed"
```

`target.name` is what was typed into the sign-in form, and `request.source_ip` where it came from. Sign-ins refused
for too many attempts are `auth.rate_limited`.

## In SQL

On the single-host Compose stack, the log is also the table `duckhaven.access.audit`, which you can query from any
workspace and join with current access in `duckhaven.access.grants`; see
[System catalog](../querying/system-catalog.md). It trails the **Audit** page by up to 10 seconds.

### List the changes to one person's access

In a worksheet, run:

```sql
SELECT event_time, action, actor_email, before, after
FROM duckhaven.access.audit
WHERE subject_email = 'ana@example.com'
ORDER BY event_time DESC;
```

Each row is a role, membership, grant or token change made to Ana's account, by anyone, newest first.

### Check who can read a catalog

```sql
SELECT principal_email, kind, level, schema_name, table_name, granted_by_email, granted_at
FROM duckhaven.access.grants
WHERE catalog_slug = 'sales' AND kind = 'catalog_grant'
ORDER BY principal_email;
```

Each row is one data grant on `sales`; empty `schema_name` and `table_name` mean the whole catalog. Every column is in
[Access and lineage tables](../reference/system-catalog-access.md#accessgrants).

## If it goes wrong

### Admin has no Audit tab

Your account lacks `audit:read`. An admin can give you the `admin` role in **Admin** → **Users**.

### `403` with `"error": "forbidden"`

The token's account lacks `audit:read`. Use an admin's token, or give the account the `admin` role in **Admin** →
**Users**. That refusal is itself recorded, as `authz.permission_denied`.

### `422` with `"error": "stale_cursor"`

The event the cursor points at was deleted by the retention purge. Start again without `cursor`.

### A change you expected is missing

- The change did not happen. A refused or failed request changes nothing and records nothing, and so does one that
  sets a value to what it already was.
- It is not an access change. Queries and other work in a workspace are in History, and storage backends,
  maintenance policy and agent lifecycle are not recorded; see
  [What is recorded](audit-log.md#what-is-recorded).
- It is outside the time range. The **Audit** page shows the last 30 days until you choose another range; the REST
  API returns every event unless you send `since` or `until`.
- It is older than `AUDIT_RETENTION_DAYS`, 365 days by default.

### SQL refused with "Reading duckhaven.access.audit needs the audit:read permission."

Your account lacks `audit:read`. An admin can give you a role that holds it, such as `admin`, in **Admin** → **Users**.

### SQL fails with `422` and `"error": "system_catalog_unavailable"`

The deployment has no system catalog, as on the HA stack, or its database is unreachable. Use the **Audit** page or the
REST API, which read the log itself; see
[Enable the system catalog database](../deployment/system-catalog.md#if-it-goes-wrong).

### A refusal shows once, though it happened many times

The same refusal by the same caller with the same details is recorded once a minute.

## Related

- [Access audit log](audit-log.md) — what is recorded, when, and for how long, and how an event reads.
- [Audit events](../reference/audit-events.md) — every action and field.
- [Manage users & access](manage-users.md) — making the changes the log records.
