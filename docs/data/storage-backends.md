---
type: concept
---

# Storage backends

A storage backend is an object-storage location, such as a bucket and prefix, where the tables of one or more catalogs
live.

An admin registers a backend once, and each [catalog](catalogs.md) uses exactly one, chosen when the catalog is
created. A [workspace](../access/workspaces.md) reaches storage only through the catalogs it attaches. A catalog can
move to another backend later with a [storage migration](migrate-catalog-storage.md). The backend is independent of the
catalog's [kind](catalogs.md#catalog-kinds): Iceberg and [DuckLake](ducklake.md) catalogs use backends the same way.

!!! info "Availability"
    The bundled object store works with no setup. External S3 and ADLS Gen2 backends need the `storage:manage`
    permission to register; see [Configure storage](configure-storage.md).

## Backend kinds

| Kind | Where the data lives | Extension the agent needs |
|---|---|---|
| `object_store` | The bundled object store, under the prefix the backend was registered with | `httpfs` |
| `s3` | An AWS S3 bucket you own | `httpfs` |
| `adls_gen2` | Azure Data Lake Storage Gen2 | `azure` |

Every backend is object storage. Agents run in their own containers, often on other hosts, and read tables over the
network with credentials vended for each query, which rules out local files. An agent serves a catalog only if it has
the extension its backend needs; see the [Agent reference](../reference/agent-reference.md).

## Credentials

External backends store **no keys**. A backend records only identifiers: an IAM **role ARN** (with an optional external
id and region) for `s3`, an Entra **tenant id** (with an optional app name and consent URL) for `adls_gen2`. Trust is
set up in your cloud account:

- **AWS S3:** Polaris assumes the registered role with STS `AssumeRole`, optionally guarded by an external id.
- **Azure ADLS Gen2:** Polaris issues a scoped SAS token through a consented Entra application.

When an agent attaches a catalog for a query, it receives short-lived credentials scoped to that catalog, which live
only as long as the query's connection. Polaris vends them for an Iceberg catalog; the API mints them for a DuckLake
catalog. No long-lived storage key is written to an agent, with one exception: a DuckLake catalog on the bundled
object store receives the store's key, limited to the catalog's prefix (see
[Enable DuckLake](../deployment/ducklake.md)).

Nothing needs rotating by hand. To revoke DuckHaven's access, remove the role's trust (S3) or the role assignment or
consent (ADLS) in your cloud account: the next vend fails, and the backend's **Test access** check turns red.

## Security baseline

DuckHaven cannot enforce what lives in your cloud account. Set these up there:

| Control | AWS S3 | Azure ADLS Gen2 |
|---|---|---|
| Identity | IAM role assumed with STS (`roleArn` + `externalId`) | Entra app and managed identity (`tenantId`, consent) |
| Least privilege | A role policy scoped to the bucket and prefix | `Storage Blob Data Contributor` on the account or container |
| Confused-deputy guard | `externalId` and an `aws:SourceArn` condition | Single-tenant consent |
| Encryption at rest | SSE-KMS, with the key scoped in the role policy | Account encryption (default); customer-managed keys optional |
| Network | Block public access, a TLS-only policy, a VPC endpoint | Private endpoints, firewall rules |

## Data layout and isolation

Each catalog has its own prefix under its backend's root: its Polaris name for an Iceberg catalog, its slug for a
DuckLake one. Catalogs on one backend therefore never collide. Choose the boundary that matches how far a mistake or a
leaked credential may reach:

- **One backend, many prefixes** (the default): one bucket or container holds many catalogs. Simple, and enough when
  one role may reach the whole bucket.
- **One backend per team**: register a separate `s3` or `adls_gen2` backend, with its own bucket or container and its
  own role, for each team that must be isolated. DuckHaven does not create buckets for you.

Set lifecycle and retention rules on the bucket or container for cost, and keep data in the region the agents run in,
to avoid cross-region transfer cost and latency.

## Backups

DuckHaven's [backups](../deployment/backup-restore.md) cover its Postgres databases, not object data. Protect the data
with your cloud's own tools, such as S3 versioning and cross-region replication, or ADLS soft delete and redundancy.

## Connection reuse

An agent reads table data from object storage over HTTP, and one scan of a large table touches hundreds of objects; a
single Iceberg table at TPC-H scale factor 10 has about 500 Parquet and metadata files. Without connection reuse, each
object costs its own TCP connection, which then waits in the kernel's `TIME_WAIT` state for a minute. A few concurrent
readers can use up the container's roughly 28,000 outbound ports in seconds, after which every connection fails until
they drain.

Agents therefore turn on `httpfs_connection_caching` for every connection, which keeps the socket count in the
hundreds. If a self-built agent image shows bursts of `Could not connect to server` errors naming a store that is
working, check this setting before you check your storage.

## Related

- [Configure storage](configure-storage.md) — register a backend and test it.
- [Catalogs](catalogs.md) — how a catalog uses a backend.
- [Migrate a catalog's storage](migrate-catalog-storage.md) — move a catalog to another backend.
- [Lakehouse maintenance](maintenance.md) — keeping tables healthy on any backend.
