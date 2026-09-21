"""Copying a DuckLake catalog into a new Iceberg one.

DuckHaven says at the moment of choice that a DuckLake table is readable by
DuckDB and nothing else, and that the choice "cannot be changed later without
copying the data". Both halves are true; the second is also the way out. This
turns the one-way door into a door.

``COPY FROM DATABASE <ducklake> TO <iceberg>`` does the whole catalog in one
statement, verified against a live Polaris REST catalog at the pinned DuckDB and
extension versions. What it copies is the **current state**, not the snapshot
history -- that is stated in the confirmation, not only in the docs, because it
is the same class of fact as the DuckDB-only warning the create dialog carries.
"""

STATUS_PENDING = "pending"
STATUS_COPYING = "copying"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = (STATUS_PENDING, STATUS_COPYING)
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)
