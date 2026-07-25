"""Elastic agent lifecycle columns

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-24

Adds the elastic-compute lifecycle columns to ``agents``. All are nullable and
NULL for a static, operator-run agent, so existing agents are untouched
(backwards compatible; Postgres stays the single state-of-record — I9).

* ``provider``      — compute backend that provisioned the agent, and the
                      discriminator that routes terminate/status/list_managed.
                      NULL = static.
* ``instance_id``   — the backend's handle for the instance (leak detection).
* ``lifecycle``     — cloud-instance state (provisioning -> running ->
                      terminating -> terminated/failed), orthogonal to presence.
* ``pool_key``      — capability/backend scope used to match demand to supply.
* ``last_active_at``— last work dispatch, driving idle scale-in.
* ``requested_cpu``/``requested_memory_gb`` — the provisioned size (vCPU + GiB),
                      so the admin UI shows size + hourly cost before dial-home.
* ``provisioned_at``/``terminated_at`` — lifecycle timestamps.

Additive — no data migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("agents", sa.Column("instance_id", sa.String(length=255), nullable=True))
    op.add_column("agents", sa.Column("lifecycle", sa.String(length=20), nullable=True))
    op.add_column("agents", sa.Column("pool_key", sa.String(length=255), nullable=True))
    op.add_column("agents", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agents", sa.Column("requested_cpu", sa.Float(), nullable=True))
    op.add_column("agents", sa.Column("requested_memory_gb", sa.Float(), nullable=True))
    op.add_column("agents", sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agents", sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "terminated_at")
    op.drop_column("agents", "provisioned_at")
    op.drop_column("agents", "requested_memory_gb")
    op.drop_column("agents", "requested_cpu")
    op.drop_column("agents", "last_active_at")
    op.drop_column("agents", "pool_key")
    op.drop_column("agents", "lifecycle")
    op.drop_column("agents", "instance_id")
    op.drop_column("agents", "provider")
