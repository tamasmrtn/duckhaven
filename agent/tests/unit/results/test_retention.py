import os
import time
import uuid

from agent.results.retention import RETAIN_SUFFIX, release, retain, sweep_once


def test_sweep_removes_only_stale_files(tmp_path):
    fresh = tmp_path / f"{uuid.uuid4()}.parquet"
    stale = tmp_path / f"{uuid.uuid4()}.parquet"
    fresh.write_bytes(b"PAR1")
    stale.write_bytes(b"PAR1")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    removed = sweep_once(tmp_path, retention_hours=24)

    assert removed == 1
    assert fresh.exists()
    assert not stale.exists()


def test_sweep_ignores_non_parquet(tmp_path):
    other = tmp_path / "keep.txt"
    other.write_bytes(b"x")
    old = time.time() - 48 * 3600
    os.utime(other, (old, old))

    removed = sweep_once(tmp_path, retention_hours=24)

    assert removed == 0
    assert other.exists()


def test_sweep_empty_dir_is_noop(tmp_path):
    assert sweep_once(tmp_path, retention_hours=24) == 0


# --- Retained results (the control plane's result cache) ---------------------


def _result(tmp_path, *, age_h: float = 0.0, size: int = 4):
    query_id = str(uuid.uuid4())
    path = tmp_path / f"{query_id}.parquet"
    path.write_bytes(b"P" * size)
    if age_h:
        then = time.time() - age_h * 3600
        os.utime(path, (then, then))
    return query_id, path


def test_a_retained_result_outlives_the_window(tmp_path):
    query_id, path = _result(tmp_path, age_h=48)
    assert retain(tmp_path, query_id, time.time() + 3600)
    assert sweep_once(tmp_path, retention_hours=24) == 0
    assert path.exists()


def test_retention_ends_at_its_deadline_or_on_release(tmp_path):
    expired, expired_path = _result(tmp_path, age_h=48)
    released, released_path = _result(tmp_path, age_h=48)
    retain(tmp_path, expired, time.time() - 1)
    retain(tmp_path, released, time.time() + 3600)
    release(tmp_path, released)

    assert sweep_once(tmp_path, retention_hours=24) == 2
    assert not expired_path.exists() and not released_path.exists()
    assert list(tmp_path.glob(f"*{RETAIN_SUFFIX}")) == []


def test_retaining_needs_a_real_result_and_a_uuid(tmp_path):
    assert not retain(tmp_path, str(uuid.uuid4()), time.time() + 60)
    assert not retain(tmp_path, "../../etc/passwd", time.time() + 60)
    release(tmp_path, "../../etc/passwd")  # never raises, never touches anything
    assert list(tmp_path.iterdir()) == []


def test_the_retained_budget_drops_the_longest_retained_first(tmp_path):
    first, first_path = _result(tmp_path, size=60)
    second, second_path = _result(tmp_path, size=60)
    retain(tmp_path, first, time.time() + 3600)
    earlier = time.time() - 600
    os.utime(tmp_path / f"{first}{RETAIN_SUFFIX}", (earlier, earlier))
    retain(tmp_path, second, time.time() + 3600)

    assert sweep_once(tmp_path, retention_hours=24, retained_max_bytes=100) == 1
    assert not first_path.exists()
    assert second_path.exists()


def test_an_orphaned_sidecar_is_cleaned_up(tmp_path):
    query_id, path = _result(tmp_path)
    retain(tmp_path, query_id, time.time() + 3600)
    path.unlink()
    sweep_once(tmp_path, retention_hours=24)
    assert list(tmp_path.iterdir()) == []
