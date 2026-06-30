"""Seamless catalog storage-backend migration.

Moves a catalog's Iceberg data from one ``StorageBackend`` to another while
preserving every snapshot and Polaris reference. Data + metadata are physically
copied to a *shadow* Polaris catalog at the target backend (with every absolute
path rewritten), the copy is verified, then the catalog is atomically re-pointed
at the shadow in a single transaction. The catalog stays read-only for writes
for the duration; reads keep working against the old location until cutover.

State is an application-managed string on ``CatalogMigration.status`` (no DB
enum), mirroring ``Query.status``.
"""

from __future__ import annotations

# Lifecycle states (CatalogMigration.status).
STATUS_PENDING = "pending"
STATUS_COPYING = "copying"
STATUS_VERIFYING = "verifying"
STATUS_CUTOVER = "cutover"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})
ACTIVE_STATUSES = frozenset({STATUS_PENDING, STATUS_COPYING, STATUS_VERIFYING, STATUS_CUTOVER})
# Writes are rejected for the whole active window (reads are never blocked). It
# spans from creation so no write can slip in between provisioning and the first
# table copy. Equal to ACTIVE_STATUSES today; named separately for intent.
FREEZE_STATUSES = ACTIVE_STATUSES

# Per-table checkpoint states (CatalogMigrationTable.status).
TABLE_PENDING = "pending"
TABLE_COPIED = "copied"
TABLE_REGISTERED = "registered"
TABLE_VERIFIED = "verified"
TABLE_FAILED = "failed"
