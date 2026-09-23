"""Copying a DuckLake catalog into a new Iceberg one.

The way out of DuckLake's DuckDB-only trade-off. ``COPY FROM DATABASE`` copies
the whole catalog's current state, not its snapshot history, in one statement.
"""

STATUS_PENDING = "pending"
STATUS_COPYING = "copying"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = (STATUS_PENDING, STATUS_COPYING)
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)
