"""CPU/memory capability detection and live-utilization sampling.

Neither ``os.cpu_count()`` nor ``psutil`` honors cgroup limits -- inside a
constrained container both report the host. So when cgroup v2 is present we read
its limits/usage directly for container-accurate numbers, and fall back to psutil
on bare metal (or non-Linux).
"""

import logging
import os
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil

from duckhaven_shared.concurrency import DEFAULT_PROFILE
from duckhaven_shared.schemas import MetricsSample

logger = logging.getLogger(__name__)

_CGROUP_BASE = Path("/sys/fs/cgroup")


def _read_cgroup_cores(base: Path) -> float | None:
    """Effective CPU cores from cgroup v2 ``cpu.max`` ("quota period").

    Returns ``None`` when the file is absent or the quota is unlimited ("max").
    """
    try:
        parts = (base / "cpu.max").read_text().split()
    except OSError:
        return None
    if len(parts) != 2 or parts[0] == "max":
        return None
    quota, period = int(parts[0]), int(parts[1])
    if period <= 0:
        return None
    return quota / period


def _read_cpu_usage_usec(base: Path) -> int | None:
    """Cumulative CPU time in microseconds from cgroup v2 ``cpu.stat``."""
    try:
        text = (base / "cpu.stat").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])
    return None


def _read_cgroup_memory_limit(base: Path) -> int | None:
    """cgroup v2 ``memory.max`` in bytes, or ``None`` when unlimited ("max") / absent."""
    try:
        raw = (base / "memory.max").read_text().strip()
    except OSError:
        return None
    if raw == "max":
        return None
    limit = int(raw)
    return limit if limit > 0 else None


def _read_memory_usage(base: Path) -> tuple[int, int] | None:
    """``(current_bytes, limit_bytes)`` from cgroup v2, or ``None`` if unlimited."""
    limit = _read_cgroup_memory_limit(base)
    if limit is None:
        return None
    try:
        current = int((base / "memory.current").read_text().strip())
    except OSError:
        return None
    return current, limit


def _cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def effective_cores(base: Path = _CGROUP_BASE) -> int:
    """Effective core count: cgroup quota (rounded) else host logical cores.

    Single source of truth for the advertised cores, the CPU% denominator, and
    the DuckDB ``threads`` setting.
    """
    cores = _read_cgroup_cores(base)
    return max(1, round(cores)) if cores else (os.cpu_count() or 1)


def effective_memory_bytes(base: Path = _CGROUP_BASE) -> int:
    """Effective memory: cgroup ``memory.max`` else total host RAM.

    Single source of truth for the advertised capability, the memory% denominator,
    and the DuckDB ``memory_limit`` setting.
    """
    limit = _read_cgroup_memory_limit(base)
    return limit if limit is not None else psutil.virtual_memory().total


def cpu_capability(base: Path = _CGROUP_BASE) -> dict[str, object]:
    """Static CPU capability advertised in AGENT_STATUS (cgroup-aware)."""
    return {
        "cores": effective_cores(base),
        "cpu_model": _cpu_model(),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
    }


class MetricsSampler:
    """Stateful sampler: computes CPU%/memory% between successive ``sample()`` calls.

    CPU% uses the cgroup ``usage_usec`` delta over wall time divided by the
    effective core count; if cgroup data is unavailable it falls back to
    ``psutil.cpu_percent``. Memory% uses ``memory.current / memory.max`` with a
    host-memory fallback.
    """

    def __init__(self, base: Path = _CGROUP_BASE) -> None:
        self._base = base
        self._cores = effective_cores(base)
        self._memory_bytes = effective_memory_bytes(base)
        self._last_usage_usec = _read_cpu_usage_usec(base)
        self._last_ts = time.monotonic()
        # Prime psutil so its first delta-based reading (the fallback path) is real.
        psutil.cpu_percent(interval=None)

    def sample(
        self,
        *,
        running_queries: int = 0,
        queued_queries: int = 0,
        active_profile: str = DEFAULT_PROFILE,
        session_count: int = 0,
    ) -> MetricsSample:
        # Resource percentages are sampled here; the admission counts/profile and
        # held-session count are supplied by the caller (channel) so this stays a
        # pure resource sampler.
        return MetricsSample(
            cpu_percent=self._cpu_percent(),
            memory_percent=self._memory_percent(),
            running_queries=running_queries,
            queued_queries=queued_queries,
            active_profile=active_profile,
            session_count=session_count,
            sampled_at=datetime.now(tz=UTC),
        )

    def _cpu_percent(self) -> float:
        usage = _read_cpu_usage_usec(self._base)
        now = time.monotonic()
        if usage is None or self._last_usage_usec is None:
            self._last_ts = now
            return round(psutil.cpu_percent(interval=None), 1)
        wall_us = (now - self._last_ts) * 1_000_000
        busy_us = usage - self._last_usage_usec
        self._last_usage_usec = usage
        self._last_ts = now
        if wall_us <= 0 or self._cores <= 0:
            return 0.0
        pct = busy_us / (wall_us * self._cores) * 100
        return round(max(0.0, min(100.0, pct)), 1)

    def _memory_percent(self) -> float:
        mem = _read_memory_usage(self._base)
        if mem is None:
            return round(psutil.virtual_memory().percent, 1)
        current, _limit = mem
        return round(max(0.0, min(100.0, current / self._memory_bytes * 100)), 1)
