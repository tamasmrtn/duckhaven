# Migrate a catalog's storage

A [catalog](../concepts/catalogs.md)'s [storage backend](../concepts/storage-backends.md) is chosen when the catalog is
created, but it is not permanent. An admin can **migrate** a catalog to a different backend — for example off the
bundled object store onto a corporate S3 bucket, or from S3 to Azure ADLS Gen 2 — preserving all data and full Iceberg
snapshot history. This guide covers the operator workflow.

## Who can migrate

Starting a migration requires the catalog's **creator** or an admin with the **catalogs** permission — the same gate as
dropping a catalog.

## What happens during a migration

DuckHaven cannot simply copy the objects to a new bucket: Iceberg references every file by **absolute** URI (metadata →
manifest lists → manifests → data files), so a raw copy would leave the new tree pointing at the old location. Instead
the runner:

1. **Provisions a shadow catalog** in Polaris at the target backend.
2. **Copies and rewrites** every table — copying each file and rewriting the old location prefix to the new one in the
   metadata and manifest files (data files are copied unchanged), then registering the rewritten table in the shadow
   catalog.
3. **Verifies** each table loads from the new location and its snapshot history matches the source.
4. **Cuts over atomically** — re-points the catalog at the new backend in a single step. The user-facing slug never
   changes, so attached workspaces and existing SQL keep working.

The old catalog and its data are retained for a configurable window
(`MIGRATION_RETENTION_DAYS`, see [Configuration](../reference/configuration.md)) after cutover, then cleaned up
automatically.

## Read-only window

While a migration is in progress the catalog is **read-only**:

- **Reads keep working** against the old location until cutover.
- **Writes are rejected** with a clear error (any `INSERT`/`UPDATE`/`DELETE`/`MERGE`/`CREATE`/`ALTER`/`DROP` against a
  workspace that has the migrating catalog attached). This is conservative by design — migrations are infrequent admin
  operations and correctness takes priority over write availability.

There is **no downtime for reads** and no data loss is possible: the catalog only ever points at the new backend after
every table has been copied and verified.

## Run a migration

1. Open **Admin → Migrations**.
2. Find the catalog in the list and click **Migrate…**.
3. Choose the **target backend** from the dropdown (the catalog's current backend is excluded) and **Start migration**.
4. The migration appears under **Migrations for &lt;catalog&gt;** with a live status badge and progress bar. Select it to
   watch the streamed log and per-table progress.

The status moves through `pending → copying → verifying → cutover → completed`. You can **Cancel** a migration any time
before cutover; the shadow catalog and its partial copy are torn down and the catalog stays on its original backend.

## If a migration fails

A failure (storage unreachable, network interruption, a process restart, …) always leaves the catalog on its **original
backend** — the backend pointer changes only in the final atomic cutover. The migration is marked `failed` with the
error shown in the log, and the partial shadow copy is cleaned up. Re-run the migration once the underlying problem is
resolved; a crashed runner resumes in-progress migrations automatically from the last completed table.

See the [operator runbook](../operations/runbook.md) for recovering a stuck migration.

## Related

- [Catalogs & Polaris](../concepts/catalogs.md#storage-migration) — the concept and consistency guarantees.
- [Storage backends](../concepts/storage-backends.md) — registering the target backend.
- [Configuration](../reference/configuration.md) — the migration runner settings.
