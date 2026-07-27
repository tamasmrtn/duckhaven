"""Compute sizing ranges and hourly cost — the single source of truth.

The operator chooses an explicit vCPU and memory amount (two sliders in the UI),
each within the range below, and the hourly cost is computed from the configured
rates. The backend owns both the range and the price formula so the UI never
computes either from stale, duplicated values — it just displays what this returns.

The *ceiling* comes from the backend rather than from a constant here, because only
the backend knows what its platform will actually run: Azure Container Instances has
a published per-group quota, and a single Docker host has however much machine it
has. A hardcoded ceiling could only ever be right for one of them — it used to be
ACI's, which meant a homelab was offered sizes with no relation to its hardware, in
both directions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from api.config import settings
from api.services.compute.backends import get_backend

logger = logging.getLogger(__name__)

# The floor and the granularity are the same everywhere: below 1 vCPU / 1 GiB a
# DuckDB agent is not worth provisioning, and both sliders step in whole units.
CPU_MIN = 1.0
CPU_STEP = 1.0
MEMORY_MIN_GB = 1.0
MEMORY_STEP_GB = 1.0

# Used when the backend has no opinion — the null backend, an unknown provider, or a
# platform that could not be reached. Deliberately small: guessing high would offer a
# size the platform then refuses, which surfaces as a provisioning failure minutes
# later rather than as a disabled slider now.
DEFAULT_CPU_MAX = 4.0
DEFAULT_MEMORY_MAX_GB = 16.0


@dataclass(frozen=True)
class Limits:
    """The size range the configured platform will actually accept."""

    cpu_min: float
    cpu_max: float
    cpu_step: float
    memory_min_gb: float
    memory_max_gb: float
    memory_step_gb: float

    def allows(self, cpu: float, memory_gb: float) -> bool:
        return (
            self.cpu_min <= cpu <= self.cpu_max
            and self.memory_min_gb <= memory_gb <= self.memory_max_gb
        )


async def limits() -> Limits:
    """Size bounds for the configured provider.

    Asks the backend for its platform ceiling and falls back to the conservative
    default when it has none. Never raises: the create-agent dialog reads this, and
    an unreachable platform should narrow the choice rather than break the page.
    """
    cpu_max, memory_max_gb = DEFAULT_CPU_MAX, DEFAULT_MEMORY_MAX_GB
    try:
        capacity = await get_backend(settings.elastic_provider).capacity()
        if capacity is not None:
            cpu_max, memory_max_gb = capacity
    except KeyError:
        logger.warning(
            "Unknown elastic provider %r; using default size limits", settings.elastic_provider
        )
    except Exception:
        logger.exception("Could not read platform capacity; using default size limits")

    return Limits(
        cpu_min=CPU_MIN,
        cpu_max=max(CPU_MIN, cpu_max),
        cpu_step=CPU_STEP,
        memory_min_gb=MEMORY_MIN_GB,
        memory_max_gb=max(MEMORY_MIN_GB, memory_max_gb),
        memory_step_gb=MEMORY_STEP_GB,
    )


def hourly_cost(cpu: float, memory_gb: float) -> float:
    """Hourly cost of a (vCPU, GiB) shape from the configured rates.

    The settings still read AZURE for historical reasons but apply to whichever
    provider is configured; a deployment with no marginal hourly cost zeroes them.
    """
    cost = (
        cpu * settings.elastic_azure_price_vcpu_hour
        + memory_gb * settings.elastic_azure_price_memory_gb_hour
    )
    return round(cost, 4)
