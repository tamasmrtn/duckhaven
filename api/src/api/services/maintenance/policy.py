"""Maintenance policy persistence (the singleton config row)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.maintenance import MaintenancePolicy
from api.services.maintenance.presets import DEFAULT_PRESET, resolve_thresholds


async def get_or_create_policy(db: AsyncSession) -> MaintenancePolicy:
    """Return the single maintenance policy, creating defaults on first access."""
    policy = (await db.execute(select(MaintenancePolicy))).scalars().first()
    if policy is None:
        policy = MaintenancePolicy(
            scan_enabled=True,
            scan_frequency="daily",
            preset=DEFAULT_PRESET,
            thresholds=resolve_thresholds(DEFAULT_PRESET),
            max_tables_per_cycle=50,
        )
        db.add(policy)
        await db.commit()
        await db.refresh(policy)
    return policy
