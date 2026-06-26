"""Admin maintenance config: read/update the policy and trigger a manual scan."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.deps import get_db, get_polaris_client, get_session_factory, require_permission
from api.models.maintenance import MaintenancePolicy
from api.models.user import User
from api.schemas.maintenance import PolicyOut, PolicyUpdate, ScanResult
from api.services.maintenance.policy import get_or_create_policy
from api.services.maintenance.presets import PRESET_NAMES, resolve_thresholds
from api.services.maintenance.scanner import run_cycle
from api.services.permissions import Permission
from api.services.polaris import PolarisClient

router = APIRouter(prefix="/maintenance")

_VALID_FREQUENCIES = {"off", "hourly", "daily"}


def _policy_out(policy: MaintenancePolicy) -> PolicyOut:
    return PolicyOut(
        scan_enabled=policy.scan_enabled,
        scan_frequency=policy.scan_frequency,
        preset=policy.preset,
        thresholds=policy.thresholds,
        max_tables_per_cycle=policy.max_tables_per_cycle,
        last_scan_at=policy.last_scan_at,
        last_deep_scan_at=policy.last_deep_scan_at,
    )


@router.get("/policy", response_model=PolicyOut)
async def get_policy(
    admin: User = Depends(require_permission(Permission.MAINTENANCE_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> PolicyOut:
    return _policy_out(await get_or_create_policy(db))


@router.put("/policy", response_model=PolicyOut)
async def update_policy(
    body: PolicyUpdate,
    admin: User = Depends(require_permission(Permission.MAINTENANCE_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> PolicyOut:
    policy = await get_or_create_policy(db)

    if body.scan_frequency is not None:
        if body.scan_frequency not in _VALID_FREQUENCIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"scan_frequency must be one of {sorted(_VALID_FREQUENCIES)}",
            )
        policy.scan_frequency = body.scan_frequency
    if body.preset is not None:
        if body.preset not in PRESET_NAMES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"preset must be one of {sorted(PRESET_NAMES)}",
            )
        policy.preset = body.preset
    if body.scan_enabled is not None:
        policy.scan_enabled = body.scan_enabled
    if body.max_tables_per_cycle is not None:
        policy.max_tables_per_cycle = max(1, body.max_tables_per_cycle)
    # A preset change or an explicit override re-resolves the threshold bundle;
    # changing the preset resets any prior advanced overrides.
    if body.preset is not None or body.thresholds is not None:
        policy.thresholds = resolve_thresholds(policy.preset, body.thresholds or {})

    await db.commit()
    await db.refresh(policy)
    return _policy_out(policy)


@router.post("/scan", response_model=ScanResult)
async def trigger_scan(
    admin: User = Depends(require_permission(Permission.MAINTENANCE_MANAGE)),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> ScanResult:
    """Run a scan cycle immediately, bypassing the cadence check."""
    result = await run_cycle(session_factory, polaris, force=True)
    return ScanResult(**{k: v for k, v in result.items() if k in ScanResult.model_fields})
