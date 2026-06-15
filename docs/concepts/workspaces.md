# Workspaces

A **workspace** is DuckHaven's central abstraction: a governance and collaboration boundary that maps one-to-one to an
Apache Polaris catalog and is pinned to exactly one [storage backend](storage-backends.md).

## What a workspace owns

- **One catalog.** Each workspace has a single Polaris catalog named after the workspace slug. See
  [Catalogs & Polaris](catalogs.md).
- **One storage backend.** The backend binding is chosen at creation time and is **immutable** — every table the
  workspace creates lives under that backend's location. See [Storage backends](storage-backends.md).
- **Members and roles.** Users join a workspace as `reader`, `writer`, or `owner`. See [Permissions](permissions.md).
- **Queries, saved queries, and history.** Everything a member runs is scoped to the workspace and recorded.

## Creating a workspace

Workspaces can be created name-only: DuckHaven auto-provisions a catalog on the bundled object store and a default
`analytics` schema, so a new team can start querying immediately. To bind a workspace to external S3 or ADLS storage,
register that [storage backend](storage-backends.md) first and select it at creation time.

!!! note "Why one backend per workspace"
    Pinning a workspace to a single backend keeps governance, credential vending, and disaster-recovery reasoning
    simple. It is one of DuckHaven's [architectural invariants](architecture.md#11-architectural-invariants).

## Related

- [Catalogs & Polaris](catalogs.md) — how the workspace catalog is governed.
- [Permissions](permissions.md) — what each role can do.
- [Manage users & access](../guides/users-access.md) — add members and assign roles.
