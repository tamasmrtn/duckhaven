"""CPU/memory capability detection and live-utilization sampling.

Neither ``os.cpu_count()`` nor ``psutil`` honors cgroup limits -- inside a
constrained container both report the host. So when cgroup v2 is present we read
its limits/usage directly for container-accurate numbers, and fall back to psutil
on bare metal (or non-Linux).

What a sample can and cannot see. CPU is a counter (``usage_usec``), so its delta
over the interval is exact however short the work was. Memory is a level, and a
level read every couple of seconds misses anything shorter than the gap: a 1.5 s
spike to 15 % was seen in only half of the measured cases. So memory's *peak* is
tracked between samples -- a light 250 ms poll plus the kernel's own
``memory.peak`` -- and reported alongside the instantaneous reading.
"""

import logging
import os
import platform
import threading
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


def _read_int(path: Path) -> int | None:
    """A single-integer cgroup file, or ``None`` when absent or unreadable."""
    try:
        return int(path.read_text().strip())
    except OSError, ValueError:
        return None


def _read_memory_current(base: Path) -> int | None:
    """This cgroup's memory use in bytes, limit or no limit.

    Reading it regardless of ``memory.max`` is what keeps an unlimited container
    reporting its *own* usage rather than the whole host's.
    """
    return _read_int(base / "memory.current")


def _read_oom_kills(base: Path) -> int | None:
    """Cumulative OOM kills in this cgroup, from ``memory.events``."""
    try:
        text = (base / "memory.events").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    return None


def _process_rss() -> int:
    """Bare-metal fallback for the memory level: this process's resident set."""
    return psutil.Process().memory_info().rss


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


class MemoryPeak:
    """The highest memory level since the last ``take()``, not just at sample instants.

    Two sources, because neither is enough alone. A daemon thread polls the level
    every ``poll_interval_s``, which catches any spike longer than the poll. The
    kernel's ``memory.peak`` is exact but lifetime-scoped and, with the cgroup
    filesystem mounted read-only as containers normally are, cannot be reset -- yet
    whenever it *rises* between two takes, that new value was reached inside the
    interval, so it is taken as-is.
    """

    def __init__(self, base: Path, poll_interval_s: float, *, start_thread: bool = True) -> None:
        self._base = base
        self._lock = threading.Lock()
        self._max: int | None = None
        self._last_lifetime = _read_int(base / "memory.peak")
        if start_thread and poll_interval_s > 0:
            thread = threading.Thread(
                target=self._poll_forever,
                args=(poll_interval_s,),
                name="memory-peak-poll",
                daemon=True,
            )
            thread.start()

    def current(self) -> int:
        """The memory level right now: the cgroup's, else this process's."""
        level = _read_memory_current(self._base)
        return level if level is not None else _process_rss()

    def poll_once(self) -> None:
        level = self.current()
        with self._lock:
            self._max = level if self._max is None else max(self._max, level)

    def _poll_forever(self, interval_s: float) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - a failed read must not end the poll
                logger.debug("Memory peak poll failed", exc_info=True)
            time.sleep(interval_s)

    def take(self) -> int:
        """Return the interval's peak and start the next interval."""
        level = self.current()
        with self._lock:
            peak = level if self._max is None else max(self._max, level)
            self._max = None
        lifetime = _read_int(self._base / "memory.peak")
        if lifetime is not None and self._last_lifetime is not None:
            if lifetime > self._last_lifetime:
                peak = max(peak, lifetime)
        if lifetime is not None:
            self._last_lifetime = lifetime
        return peak


class MetricsSampler:
    """Stateful sampler: computes CPU%/memory% between successive ``sample()`` calls.

    CPU% uses the cgroup ``usage_usec`` delta over wall time divided by the
    effective core count; if cgroup data is unavailable it falls back to
    ``psutil.cpu_percent``. Memory% is this cgroup's ``memory.current`` over the
    effective memory (its limit, else host RAM); on bare metal the level is the
    process's resident set. Each sample also carries the interval's memory peak
    (see :class:`MemoryPeak`) and OOM kills.

    One sampler lives for the agent's lifetime; :meth:`rebase` is called on each
    reconnect so the first sample after an outage does not span it.
    """

    def __init__(
        self,
        base: Path = _CGROUP_BASE,
        *,
        memory_poll_interval_s: float = 0.25,
    ) -> None:
        self._base = base
        self._cores = effective_cores(base)
        self._memory_bytes = effective_memory_bytes(base)
        self._memory_peak = MemoryPeak(
            base, memory_poll_interval_s, start_thread=memory_poll_interval_s > 0
        )
        self._last_usage_usec = _read_cpu_usage_usec(base)
        self._last_oom_kills = _read_oom_kills(base)
        self._last_ts = time.monotonic()
        # Prime psutil so its first delta-based reading (the fallback path) is real.
        psutil.cpu_percent(interval=None)

    def rebase(self) -> None:
        """Start a fresh interval now, discarding whatever accrued since the last sample."""
        self._last_usage_usec = _read_cpu_usage_usec(self._base)
        self._last_oom_kills = _read_oom_kills(self._base)
        self._last_ts = time.monotonic()
        self._memory_peak.take()

    def sample(
        self,
        *,
        running_queries: int = 0,
        queued_queries: int = 0,
        active_profile: str = DEFAULT_PROFILE,
        session_count: int = 0,
        growth_waiting: int = 0,
        estimates_abandoned: int = 0,
        executing_queries: int | None = None,
        idle_sessions: int | None = None,
    ) -> MetricsSample:
        # Resource percentages are sampled here; the admission counts/profile and
        # held-session count are supplied by the caller (channel) so this stays a
        # pure resource sampler.
        now = time.monotonic()
        interval_s = now - self._last_ts
        usage = _read_cpu_usage_usec(self._base)
        oom_kills = _read_oom_kills(self._base)
        oom_delta = (
            max(0, oom_kills - self._last_oom_kills)
            if oom_kills is not None and self._last_oom_kills is not None
            else None
        )
        self._last_oom_kills = oom_kills
        return MetricsSample(
            cpu_percent=self._cpu_percent(usage, now),
            memory_percent=self._percent_of_memory(self._memory_peak.current()),
            memory_peak_percent=self._percent_of_memory(self._memory_peak.take()),
            running_queries=running_queries,
            queued_queries=queued_queries,
            active_profile=active_profile,
            session_count=session_count,
            growth_waiting=growth_waiting,
            estimates_abandoned=estimates_abandoned,
            executing_queries=executing_queries,
            idle_sessions=idle_sessions,
            interval_s=round(interval_s, 3),
            oom_kills=oom_delta,
            cpu_seconds_total=round(usage / 1_000_000, 3) if usage is not None else None,
            oom_kills_total=oom_kills,
            sampled_at=datetime.now(tz=UTC),
        )

    def _cpu_percent(self, usage: int | None, now: float) -> float:
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

    def _percent_of_memory(self, level: int) -> float:
        return round(max(0.0, min(100.0, level / self._memory_bytes * 100)), 1)
