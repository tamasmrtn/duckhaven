# Storage backends

A **storage backend** is a physical location where table data lives. Backends are registered once by an admin
and referenced by [catalogs](catalogs.md) — one backend per catalog. A catalog's backend is chosen at creation, but it
is no longer permanent: an admin can move a catalog to a different backend with a
[storage migration](catalogs.md#storage-migration). A [workspace](workspaces.md) reaches storage through the catalogs
it attaches.

A backend is independent of a catalog's [kind](catalogs.md#catalog-kinds): Iceberg and [DuckLake](ducklake.md)
catalogs both bind to one the same way, and a workspace can reach both through the catalogs it attaches.

## S3-compatible object storage only

Every backend is S3-compatible object storage. This is forced by DuckHaven's control-plane / compute split: DuckDB can
only read and write Iceberg tables through the REST catalog when [Polaris](catalogs.md) can vend scoped credentials the
remote [agent](agents.md) uses, which rules out local-file storage across the container boundary.

## Backend kinds

| Kind | Physical location | Required extension |
|---|---|---|
| `object_store` | Bundled object-store bucket, under the prefix the backend was registered with | `httpfs` |
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

## Connection reuse

An agent reads table data straight from object storage over HTTP, and a single scan of a
large table touches hundreds of objects — one Iceberg table at TPC-H SF10 is around 500
Parquet and metadata files. DuckDB's HTTP layer does **not** reuse connections by default,
so each of those objects would cost its own TCP connection, and each finished connection
would sit in the kernel's `TIME_WAIT` state for a minute afterwards.

That is fast enough to run a container out of outbound ports. The Linux default range is
about 28,000, and a handful of concurrent readers can burn through all of them in seconds;
every connection after that fails until the backlog drains. The agent therefore turns
`httpfs_connection_caching` on for every connection it opens, which keeps the count of
sockets in the hundreds rather than the tens of thousands.

!!! note "Why it is worth stating"
    Before this was enabled, a burst of concurrent readers looked like a *storage* outage —
    DuckDB reports the exhausted-port error as `Could not connect to server`, naming the
    object store, which is the one component that was working fine. If you see that error
    on a self-built agent image, check this setting before you check your storage.

## Related

- [Configure storage](../deployment/storage.md) — register and bind a backend.
- [Catalogs & Polaris](catalogs.md) — how a catalog binds to a backend, and how a workspace reaches it.
