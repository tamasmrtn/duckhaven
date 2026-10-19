---
type: how-to
---

# Migrate a catalog's storage

Move a catalog to another storage backend, such as from the bundled object store to an S3 bucket, with all its data
and snapshot history.

The catalog keeps its name, so workspaces and SQL that use it keep working. Reads work throughout; writes are refused
until the move finishes.

## Before you begin

- **You need:** the `storage:manage` permission to open **Admin → Migrations**, and to be the catalog's creator or hold
  `catalogs:admin`. The `admin` role has all three.
- **Applies to:** Iceberg and DuckLake catalogs. The target backend must be registered and pass **Test access**; see
  [Configure storage](configure-storage.md).
- **Plan for:** a write freeze on the catalog while it copies, which grows with the catalog's size.

## How the copy works

**Iceberg** metadata names every file by its full path, so copying the objects alone would leave the new tree pointing
at the old location. DuckHaven creates a shadow catalog in Polaris on the target backend, copies each table's files
while rewriting the old location inside the metadata and manifests, registers the rewritten tables, and verifies that
each loads and keeps its snapshot history.

**DuckLake** records file paths relative to the catalog's data path, so DuckHaven copies the files and changes that one
setting.

Either way the catalog is re-pointed at the new backend in one atomic step, only after every table is copied and
verified. Until then it stays on the original backend, so a failure never loses data. The old data is kept for
`MIGRATION_RETENTION_DAYS` (7 by default) after the switch, then removed.

## Run a migration

1. Open **Admin → Migrations**.
2. Find the catalog and click **Migrate…**.
3. Choose the target backend (the current one is not offered) and click **Start migration**.
4. The migration appears under the catalog with a status and progress bar. Select it to follow its log and per-table
   progress.

The status moves through `pending`, `copying`, `verifying`, `cutover` and `completed`. Until `cutover`, **Cancel** stops
it, removes the partial copy and leaves the catalog where it was.

While it runs, a write to the catalog, from any workspace that attaches it, fails with
`A storage backend migration is in progress; the catalog is read-only.` Reads continue against the old location.

## Check that it worked

The status reads `completed`, and the catalog's **Catalog information** names the new backend. Query a table to
confirm it reads.

## If it goes wrong

### The migration is `failed`

The catalog is still on its original backend: the switch happens only at the end. Read the error in the log, fix the
cause, such as an unreachable backend, and start a new migration. The failed run's shadow catalog is removed on a best
effort basis; if a Polaris catalog named `⟨name⟩__m⟨hex⟩` remains, drop it in Polaris.

### It stays in `copying` or `verifying`

Check that `MIGRATION_RUNNER_ENABLED` is `true` on at least one healthy API replica, and that the target backend passes
**Test access**. The runner retries passing storage and Polaris errors, and a restarted runner resumes from the last
completed table; a backend that stays unreachable ends the migration as `failed`.

### "… data file(s) in '⟨catalog⟩' are recorded with an absolute path and will not move with the catalog"

The DuckLake catalog has files that were registered in place with `ducklake_add_data_files` rather than written by
DuckHaven. They would not move with the catalog, and their path may name a bucket the new backend cannot reach, so the
migration is refused with **422**. Rewrite or drop those tables first.

### "A migration is already in progress for this catalog."

Wait for it to finish, or cancel it.

### You want to undo a completed migration

Within `MIGRATION_RETENTION_DAYS` of the switch, start a new migration back to the original backend.

## Related

- [Storage backends](storage-backends.md) — registering the target backend.
- [Catalogs](catalogs.md#storage-migration) — what a migration changes for users.
- [REST API → Catalog storage migrations](../reference/rest-api.md#catalog-storage-migrations) — the same steps from a
  script.
