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

## Related

- [Catalogs](../data/catalogs.md) — what a workspace attaches.
- [How access works](how-access-works.md) — roles and the other access layers.
- [Manage catalogs](../data/manage-catalogs.md) — create, attach, detach and drop catalogs.
- [Manage users & access](manage-users.md) — add members and assign roles.
