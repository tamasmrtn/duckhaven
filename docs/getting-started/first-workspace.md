# Your first workspace

A [workspace](../concepts/workspaces.md) is where your team collaborates: it maps to one Apache Polaris catalog and is
pinned to one [storage backend](../concepts/storage-backends.md). This page creates your first one.

## Create a workspace

After signing in as the admin you created during [installation](installation.md), create a workspace by name. Name-only
creation automatically:

- provisions a catalog on the bundled object store, and
- creates a default `analytics` schema,

so you can start querying immediately — no storage configuration required.

!!! tip "Using external storage"
    To back a workspace with your own S3 bucket or Azure Data Lake instead of the bundled object store, register that
    [storage backend](../deployment/storage.md) first, then select it when creating the workspace. The backend binding
    is **immutable** after creation.

## Add members

Invite teammates and assign each a role:

- `reader` — browse and query
- `writer` — also create and modify tables
- `owner` — also manage membership

See [Manage users & access](../guides/users-access.md) and [Permissions](../concepts/permissions.md).

## Next steps

- [Run your first query](first-query.md).
- [Manage catalogs](../guides/manage-catalogs.md) — create schemas and tables.
