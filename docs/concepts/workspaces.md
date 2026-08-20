# Workspaces

A **workspace** is DuckHaven's governance and collaboration boundary: it groups people and the work they run. It
**attaches one or more [catalogs](catalogs.md)** — the data domains a team works with — rather than owning a single
catalog. A workspace is best thought of as a DTAP environment (e.g. `dev`, `prod`) that brings together the catalogs
relevant to it.

## What a workspace owns

- **A set of attached catalogs.** A workspace attaches any number of catalogs, and one of them is the **default** —
  the catalog used for unqualified table names. Catalogs are first-class entities decoupled from workspaces (see
  [Catalogs & Polaris](catalogs.md)); the **same catalog can be attached to several workspaces** (for example a shared
  `raw` catalog visible from both `dev` and `prod`). Storage lives on the catalog, not the workspace.
- **Members and roles.** Users join a workspace as `reader`, `writer`, or `owner`. See [Permissions](permissions.md).
- **Queries, saved queries, and history.** Everything a member runs is scoped to the workspace and recorded.

## Creating a workspace

Workspaces are created **name-only** and start **empty** — no catalog, no storage. An owner then **creates a catalog**
(its own Polaris catalog + storage; bundled object storage by default, or an external S3/ADLS backend) or **attaches an
existing one**. The first catalog attached becomes the workspace's default. See
[Manage catalogs](../guides/manage-catalogs.md).

!!! note "Shared catalogs"
    Because a catalog can be attached to multiple workspaces, members of any attaching workspace can reach it. This is
    the intended sharing semantic; a catalog cannot be dropped while any workspace still has it attached.

## Settings: rename, describe, and delete

An owner manages a workspace's identity from **Settings → Workspace**, reachable from the user menu in the top bar.

- **Rename** and **description** are plain identity edits — the workspace's routable slug (the `/$ws/...` segment in
  every URL) does not change, so existing links and bookmarks keep working.
- **Delete** is permanent and requires typing the workspace's name to confirm. It removes the workspace's queries,
  saved queries, schedules, and assistant conversation history — none of that is recoverable. **Attached catalogs are
  not affected**: because a catalog is a decoupled, potentially shared entity (see the note above), deleting a
  workspace only detaches it — the catalog and its data remain intact for any other workspace it is attached to.

Both actions require the `owner` role — the same tier that already gates adding and removing members.

## Related

- [Catalogs & Polaris](catalogs.md) — how catalogs are governed and shared.
- [Permissions](permissions.md) — what each role can do.
- [Manage catalogs](../guides/manage-catalogs.md) — create, attach, detach, and drop catalogs.
- [Manage users & access](../guides/users-access.md) — add members and assign roles.
