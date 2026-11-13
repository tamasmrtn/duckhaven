---
type: concept
---

# Workspaces

A workspace is where a team works: its members and their roles, the catalogs it uses, and the worksheets, saved
queries and history of everything run in it.

A workspace does not own data. It **attaches** [catalogs](../data/catalogs.md), and the same catalog can be attached to
several workspaces — a shared `raw` catalog visible from both `dev` and `prod`, for example. Storage belongs to the
catalog, not the workspace. A common pattern is one workspace per environment or per team, each attaching the catalogs
relevant to it.

## What a workspace holds

- **Attached catalogs.** Any number; one is the **default**, used for unqualified table names. The first catalog
  attached becomes the default.
- **Members and roles.** People and service accounts join as `reader`, `writer` or `owner`; see
  [How access works](how-access-works.md).
- **Work.** Worksheets, saved queries, schedules, assistant conversations, and the history of every run.

A new workspace starts empty, with no catalogs. An owner then creates a catalog — on the bundled object store by
default, or on an S3 or ADLS backend — or attaches an existing one; see [Manage catalogs](../data/manage-catalogs.md).

!!! note "Shared catalogs"
    Every member of every workspace that attaches a catalog can reach it, at their role in that workspace. A catalog
    cannot be dropped while any workspace still attaches it.

## Renaming and deleting a workspace

An owner changes a workspace from **Settings** → **Workspace**, in the user menu at the top right.

- **Renaming** it, or changing its description, keeps its slug — the `/⟨slug⟩/…` part of every URL — so links and
  bookmarks keep working.
- **Deleting** it is permanent and asks you to type its name. Its queries, saved queries, schedules and assistant
  conversations are deleted. Its catalogs are **not**: the workspace is detached from them, and their data stays
  available to any other workspace that attaches them.

## Default compute

An owner can choose one [agent](../compute/agents.md) as the workspace's **default compute** from **Settings** →
**Workspace**. It runs everything in the workspace that names no agent: the assistant, MCP, SQL sessions, describes,
and schedules or saved queries that have no agent of their own. A worksheet with no agent of its own starts on it.

Every member of the workspace can run on the default, whether or not a grant reaches them. Data access is unchanged:
catalog grants and row and column rules still apply to each person. Setting it needs the `use` tier on an `open`
agent, and the `operate` tier on a [restricted](../access/agent-access.md#open-and-restricted-agents) one, because it
opens that agent to the whole workspace.

The default is the workspace's, not the person's who set it. It keeps working if they later lose access to the agent,
so check who can reach an agent before making it a default. Agent admins see the workspaces that use an agent on its
**Access** tab.

If the default is a stopped [elastic agent](../compute/elastic-compute.md), running anything starts it. If it cannot
run the work, the request fails and names the agent and the reason; DuckHaven does not choose another. Clearing the
default returns the workspace to automatic choice. Deleting the agent clears it too.

## Time zone

A workspace's time zone decides what **today** is for the semantic layer's relative
[time windows](../metadata/semantic-layer.md#why-every-metric-names-a-time-column), so month to date on the 1st means
this month wherever the server runs. It is UTC until an owner sets an IANA name, such as `Europe/London`, with
`timezone` on `PATCH /api/workspaces/{workspace}`; **Settings** has no control for it. A name that is not in the IANA
database, such as `+01:00` or `europe/london`, is refused with a 422.

The time zone moves only the dates a window resolves to. Schedules still run on cron expressions in UTC, and
timestamps in results are unchanged. For a `TIMESTAMPTZ` time dimension, a window's edges are midnight in the agent's
time zone, usually UTC, rather than the workspace's, so a row stamped within a few hours of midnight can land in the
neighbouring day. A `DATE` or plain `TIMESTAMP` column has no such gap.

## Related

- [Catalogs](../data/catalogs.md) — what a workspace attaches.
- [How access works](how-access-works.md) — roles and the other access layers.
- [Manage catalogs](../data/manage-catalogs.md) — create, attach, detach and drop catalogs.
- [Manage users & access](manage-users.md) — add members and assign roles.
