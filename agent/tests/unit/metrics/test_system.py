import os

import agent.metrics.system as sysm


def test_cpu_capability_uses_cgroup_effective_cores(tmp_path):
    """cpu.max "quota period" advertises the container's effective core count."""
    (tmp_path / "cpu.max").write_text("200000 100000")  # 2 cores
    cap = sysm.cpu_capability(base=tmp_path)
    assert cap["cores"] == 2
    assert "cpu_model" in cap
    assert "cpu_cores_physical" in cap


def test_cpu_capability_falls_back_to_os_count_when_unlimited(tmp_path):
    """With no cgroup quota the host logical core count is advertised."""
    cap = sysm.cpu_capability(base=tmp_path)  # empty dir, no cpu.max
    assert cap["cores"] == (os.cpu_count() or 1)

    (tmp_path / "cpu.max").write_text("max 100000")  # unlimited quota
    assert sysm.cpu_capability(base=tmp_path)["cores"] == (os.cpu_count() or 1)


def test_cpu_percent_from_cgroup_usage_delta(tmp_path, monkeypatch):
    """CPU% = usage_usec delta / (wall * effective cores). 0.5 cpu-s over 1s wall
    on 1 core = 50%."""
    (tmp_path / "cpu.max").write_text("100000 100000")  # 1 effective core
    (tmp_path / "cpu.stat").write_text("usage_usec 0\n")

    ticks = iter([0.0, 1.0])  # __init__ reads 0.0; sample() reads 1.0 (1s elapsed)
    monkeypatch.setattr(sysm.time, "monotonic", lambda: next(ticks))

    sampler = sysm.MetricsSampler(base=tmp_path)
    (tmp_path / "cpu.stat").write_text("usage_usec 500000\n")  # 0.5 cpu-seconds
    assert sampler.sample().cpu_percent == 50.0


def test_effective_memory_bytes_uses_cgroup_limit(tmp_path):
    """A concrete memory.max is the effective (advertised + enforced) memory."""
    (tmp_path / "memory.max").write_text("4294967296\n")  # 4 GiB
    assert sysm.effective_memory_bytes(base=tmp_path) == 4294967296


def test_effective_memory_bytes_falls_back_to_host(tmp_path, monkeypatch):
    """memory.max == "max" or absent => total host RAM."""

    class FakeVM:
        total = 17179869184  # 16 GiB

    monkeypatch.setattr(sysm.psutil, "virtual_memory", lambda: FakeVM())

    assert sysm.effective_memory_bytes(base=tmp_path) == FakeVM.total  # no file

    (tmp_path / "memory.max").write_text("max\n")
    assert sysm.effective_memory_bytes(base=tmp_path) == FakeVM.total


def test_advertised_memory_gb_rounds_to_one_decimal(tmp_path):
    """Host RAM is rarely a round GB; the advertised value rounds to 1 decimal."""
    (tmp_path / "memory.max").write_text(str(15_500_000_000) + "\n")  # ~14.43 GiB
    gb = round(sysm.effective_memory_bytes(base=tmp_path) / 1024**3, 1)
    assert gb == 14.4


def test_memory_percent_from_cgroup(tmp_path):
    """Memory% = memory.current / memory.max."""
    (tmp_path / "memory.current").write_text("512\n")
    (tmp_path / "memory.max").write_text("1024\n")
    sampler = sysm.MetricsSampler(base=tmp_path)
    assert sampler.sample().memory_percent == 50.0


def test_sampler_falls_back_to_host_without_cgroup(tmp_path):
    """With no cgroup files the sampler still yields sane host-level percentages."""
    sampler = sysm.MetricsSampler(base=tmp_path)  # empty dir
    s = sampler.sample()
    assert 0.0 <= s.cpu_percent <= 100.0
    assert 0.0 <= s.memory_percent <= 100.0


def test_allocation_has_single_source_of_truth(tmp_path, monkeypatch):
    """The advertised capability, the utilization denominator, and the admission
    budget all derive from effective_memory_bytes/effective_cores -- so they can
    never diverge. (The runner then executes within the slice admission grants.)"""
    import agent.control.channel as channel
    import agent.executor.admission as admission_mod

    mem = 9 * 1024**3
    cores = 5
    for mod in (sysm, channel, admission_mod):
        monkeypatch.setattr(mod, "effective_memory_bytes", lambda *a, **k: mem, raising=False)
        monkeypatch.setattr(mod, "effective_cores", lambda *a, **k: cores, raising=False)

    # (a) advertised capability
    caps = channel._get_capabilities()
    assert caps.memory_limit_gb == round(mem / 1024**3, 1)
    assert caps.cores == cores

    # (b) live-utilization denominators
    sampler = sysm.MetricsSampler(base=tmp_path)
    assert sampler._memory_bytes == mem
    assert sampler._cores == cores

    # (c) admission budget: a single full slot equals the whole detected memory
    # (no headroom) and its threads equal the detected cores.
    adm = admission_mod.Admission(profile="single", headroom=0.0)
    assert adm._budget == mem
    assert adm._slots[0].memory_bytes == mem
    assert adm._slots[0].threads == cores


def _cgroup(tmp_path, *, current: int, limit: int = 1000, peak: int | None = None):
    (tmp_path / "memory.max").write_text(f"{limit}\n")
    (tmp_path / "memory.current").write_text(f"{current}\n")
    if peak is not None:
        (tmp_path / "memory.peak").write_text(f"{peak}\n")


def test_memory_percent_uses_own_cgroup_when_unlimited(tmp_path, monkeypatch):
    """No limit: the level is still this cgroup's own usage, over host RAM, never the
    whole host's usage percentage."""

    class FakeVM:
        total = 4000
        percent = 93.0  # what the host as a whole is using

    monkeypatch.setattr(sysm.psutil, "virtual_memory", lambda: FakeVM())
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "memory.current").write_text("1000\n")
    sampler = sysm.MetricsSampler(base=tmp_path, memory_poll_interval_s=0)
    assert sampler.sample().memory_percent == 25.0


def test_poll_catches_a_spike_between_samples(tmp_path):
    """A spike that starts and ends inside one interval is the reason for the poll."""
    _cgroup(tmp_path, current=100)
    sampler = sysm.MetricsSampler(base=tmp_path, memory_poll_interval_s=0)
    (tmp_path / "memory.current").write_text("800\n")
    sampler._memory_peak.poll_once()
    (tmp_path / "memory.current").write_text("100\n")

    s = sampler.sample()
    assert s.memory_percent == 10.0
    assert s.memory_peak_percent == 80.0
    # The next interval starts from scratch.
    assert sampler.sample().memory_peak_percent == 10.0


def test_a_rise_in_the_lifetime_peak_is_this_intervals_peak(tmp_path):
    """memory.peak can't be reset on a read-only cgroupfs, but when it rises between
    two samples the new value was reached inside the interval — exactly."""
    _cgroup(tmp_path, current=100, peak=300)
    sampler = sysm.MetricsSampler(base=tmp_path, memory_poll_interval_s=0)
    (tmp_path / "memory.peak").write_text("950\n")
    assert sampler.sample().memory_peak_percent == 95.0
    # Unchanged lifetime peak says nothing about this interval.
    assert sampler.sample().memory_peak_percent == 10.0


def test_oom_kills_are_counted_per_interval_and_in_total(tmp_path):
    _cgroup(tmp_path, current=100)
    (tmp_path / "memory.events").write_text("low 0\nhigh 0\nmax 4\noom 1\noom_kill 1\n")
    sampler = sysm.MetricsSampler(base=tmp_path, memory_poll_interval_s=0)
    (tmp_path / "memory.events").write_text("low 0\nhigh 0\nmax 9\noom 3\noom_kill 3\n")
    s = sampler.sample()
    assert s.oom_kills == 2
    assert s.oom_kills_total == 3
    assert sampler.sample().oom_kills == 0


def test_fields_it_cannot_measure_are_none_not_zero(tmp_path):
    """Without cgroup files there is no OOM counter or CPU counter to report."""
    s = sysm.MetricsSampler(base=tmp_path, memory_poll_interval_s=0).sample()
    assert s.oom_kills is None
    assert s.oom_kills_total is None
    assert s.cpu_seconds_total is None
    assert s.memory_peak_percent is not None  # the process level is always readable


def test_interval_and_rebase(tmp_path, monkeypatch):
    """The first sample after a reconnect covers only the time since it, not the outage."""
    (tmp_path / "cpu.max").write_text("100000 100000")
    (tmp_path / "cpu.stat").write_text("usage_usec 0\n")
    ticks = iter([0.0, 100.0, 101.0, 101.0])
    monkeypatch.setattr(sysm.time, "monotonic", lambda: next(ticks))
    sampler = sysm.MetricsSampler(base=tmp_path, memory_poll_interval_s=0)

    # 100 s disconnected, busy the whole time as far as the counter knows.
    (tmp_path / "cpu.stat").write_text("usage_usec 100000000\n")
    sampler.rebase()  # reads t=100
    (tmp_path / "cpu.stat").write_text("usage_usec 100500000\n")
    s = sampler.sample()  # reads t=101
    assert s.interval_s == 1.0
    assert s.cpu_percent == 50.0
    assert s.cpu_seconds_total == 100.5
