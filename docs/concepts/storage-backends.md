# Storage backends

A **storage backend** is a physical location where Iceberg table data lives. Backends are registered once by an admin
and referenced by [catalogs](catalogs.md) — one backend per catalog. A catalog's backend is chosen at creation, but it
is no longer permanent: an admin can move a catalog to a different backend with a
[storage migration](catalogs.md#storage-migration). A [workspace](workspaces.md) reaches storage through the catalogs
it attaches.

## S3-compatible object storage only

Every backend is S3-compatible object storage. This is forced by DuckHaven's control-plane / compute split: DuckDB can
only read and write Iceberg tables through the REST catalog when [Polaris](catalogs.md) can vend scoped credentials the
remote [agent](agents.md) uses, which rules out local-file storage across the container boundary.

## Backend kinds

| Kind | Physical location | Required extension |
|---|---|---|
| `object_store` | Bundled MinIO bucket (per-workspace `/{slug}` prefix) | `httpfs` |
| `s3` | External, operator-owned S3 bucket | `httpfs` |
| `adls_gen2` | Azure Data Lake Storage Gen 2 | `azure` |

An agent must have the required DuckDB extension loaded to serve a workspace on a given backend; see the
[Agent reference](../reference/agent-reference.md).

## Credential model

External backends carry **no static keys**. Each backend stores only identifiers — for `s3` an IAM **role ARN** (plus
an optional external id and region), for `adls_gen2` an Entra **tenant id** (plus an optional app name and consent
URL). Trust is established on the cloud side:

- **AWS S3** — Polaris assumes the registered role via STS (`AssumeRole`), optionally guarded by an external id.
- **Azure ADLS Gen2** — Polaris vends a scoped SAS token through a consented Entra application in the tenant.

## Credential vending

When an agent attaches a workspace catalog, Polaris vends short-lived, connection-scoped credentials applied as a
DuckDB `SECRET` that dies with the per-query connection. No long-lived storage secrets are stored on agents — the role
assumption / SAS minting happens server-side in Polaris, and only the resulting scoped, expiring credential ever
reaches DuckDB.

## Related

- [Configure storage](../deployment/storage.md) — register and bind a backend.
- [Workspaces](workspaces.md) — how a workspace pins to a backend.
