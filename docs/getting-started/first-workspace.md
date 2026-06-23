# Your first workspace

A [workspace](../concepts/workspaces.md) is where your team collaborates. It attaches one or more
[catalogs](../concepts/catalogs.md) — the data domains you query. This page creates your first workspace and catalog.

## Create a workspace

After signing in as the admin you created during [installation](installation.md), create a workspace **by name**. A new
workspace starts **empty** — no catalog and no storage are provisioned yet.

## Create a catalog

Open the workspace's catalog browser and use the **+ → Create catalog**. By default the catalog uses the bundled object
storage and gets a default `analytics` schema, so you can start querying immediately — no storage configuration
required. The first catalog you attach becomes the workspace's default (used for unqualified table names).

!!! tip "Using external storage"
    To back a catalog with your own S3 bucket or Azure Data Lake instead of the bundled object store, pick or register
    that [storage backend](../deployment/storage.md) in the create-catalog dialog's **Storage backend** selector. A
    catalog's backend is **immutable** after creation.

## Add members

Invite teammates and assign each a role:

- `reader` — browse and query
- `writer` — also create and modify tables
- `owner` — also manage membership

See [Manage users & access](../guides/users-access.md) and [Permissions](../concepts/permissions.md).

## Next steps

- [Run your first query](first-query.md).
- [Manage catalogs](../guides/manage-catalogs.md) — create schemas and tables.
