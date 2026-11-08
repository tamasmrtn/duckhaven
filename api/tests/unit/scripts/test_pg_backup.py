"""Tests for scripts/pg-backup.sh: the files it writes are readable by their owner only.

The script runs against a stub ``docker`` first on PATH, which answers the
database probe and prints a fixed dump, so no Postgres or Docker is needed.
"""

from __future__ import annotations

import gzip
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "pg-backup.sh"

_STUB_DOCKER = """#!/bin/sh
case "$*" in
    *psql*) echo 1 ;;
    *pg_dump*) echo "-- dump" ;;
esac
"""


def _run(tmp_path: Path, backup_dir: Path) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(_STUB_DOCKER)
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DUCKHAVEN_BACKUP_DIR": str(backup_dir),
    }
    # A permissive caller umask, as an interactive shell or cron usually has.
    return subprocess.run(
        ["bash", "-c", 'umask 022; exec bash "$0"', str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_dumps_and_their_directory_are_owner_only(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    _run(tmp_path, backup_dir)

    dumps = sorted(backup_dir.iterdir())
    assert [d.name.split("_", 1)[0] for d in dumps] == ["duckhaven", "ducklake", "polaris"]
    assert _mode(backup_dir) == 0o700
    for dump in dumps:
        assert _mode(dump) == 0o600
        assert gzip.decompress(dump.read_bytes()) == b"-- dump\n"


def test_an_existing_directory_is_closed_off(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_dir.chmod(0o755)

    _run(tmp_path, backup_dir)

    assert _mode(backup_dir) == 0o700


def test_a_directory_it_cannot_restrict_is_still_backed_up(tmp_path: Path) -> None:
    """A mount that refuses chmod gets a warning, not a skipped backup."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    chmod = bin_dir / "chmod"
    chmod.write_text("#!/bin/sh\nexit 1\n")
    chmod.chmod(0o755)

    result = _run(tmp_path, backup_dir)

    assert "could not restrict" in result.stderr
    assert len(list(backup_dir.iterdir())) == 3
