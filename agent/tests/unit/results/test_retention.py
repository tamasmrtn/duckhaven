import os
import time
import uuid

from agent.results.retention import sweep_once


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
