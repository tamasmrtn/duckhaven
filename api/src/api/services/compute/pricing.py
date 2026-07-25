"""Compute sizing ranges and hourly cost — the single source of truth.

The operator chooses an explicit vCPU and memory amount (two sliders in the UI),
each within the ranges below, and the hourly cost is computed from the configured
ACI rates. The backend owns both the ranges and the price formula so the UI never
computes cost from stale, duplicated rates — it just displays what this returns.
"""

from __future__ import annotations

from api.config import settings

# Azure Container Instances per-container-group limits (most regions).
CPU_MIN = 1.0
CPU_MAX = 4.0
CPU_STEP = 1.0
MEMORY_MIN_GB = 1.0
MEMORY_MAX_GB = 16.0
MEMORY_STEP_GB = 1.0


def clamp_cpu(cpu: float) -> bool:
    return CPU_MIN <= cpu <= CPU_MAX


def clamp_memory(memory_gb: float) -> bool:
    return MEMORY_MIN_GB <= memory_gb <= MEMORY_MAX_GB


def hourly_cost(cpu: float, memory_gb: float) -> float:
    """Hourly cost of a (vCPU, GiB) shape from the configured ACI rates."""
    cost = (
        cpu * settings.elastic_azure_price_vcpu_hour
        + memory_gb * settings.elastic_azure_price_memory_gb_hour
    )
    return round(cost, 4)
