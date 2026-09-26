"""Data written on one agent runtime reads back on another, in both directions.

Agents on different runtimes share catalogs, and the API decodes every agent's
result pages with its own (default-runtime) DuckDB. These pin what that sharing
may assume, against a live Polaris, object store and DuckLake metadata database:

- Iceberg tables written by either runtime read on the other;
- a DuckLake catalog the default runtime created is read and written by the other
  without being migrated, so the default can still open it afterwards;
- a DuckLake catalog a newer runtime creates is one the default cannot open — the
  one-way format move the API's dispatch gate exists to prevent. Should a future
  build stop doing that, this fails, and the manifest's formats need updating;
- result pages written by either runtime decode on the other.

Each runtime runs in its own virtualenv (`make test-agent-runtime RUNTIME=<id>`
builds `.venv-duckdb<id>`); a runtime without one is skipped. Opt-in:
`pytest tests/runtime_compat -m runtime_compat`, nightly in CI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from testkit import polaris as dh_polaris

from duckhaven_shared.runtimes import DEFAULT_RUNTIME_ID, RUNTIMES

pytestmark = pytest.mark.runtime_compat

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name("worker.py")

OTHERS = [r.id for r in RUNTIMES.values() if r.id != DEFAULT_RUNTIME_ID and r.status != "retired"]


def _python(runtime_id: str) -> str:
    if runtime_id == DEFAULT_RUNTIME_ID:
        return sys.executable
    path = ROOT / f".venv-duckdb{runtime_id}" / "bin" / "python"
    if not path.exists():
        pytest.skip(f"no virtualenv for runtime {runtime_id}; run make test-agent-runtime")
    return str(path)


def _run(runtime_id: str, action: str, **args) -> dict:
    out = subprocess.run(
        [_python(runtime_id), str(WORKER), action, json.dumps(args)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=180,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def _ok(result: dict) -> dict:
    assert result["ok"], result
    return result


@pytest.fixture
async def iceberg_catalog():
    base = os.getenv("POLARIS_BASE_URL")
    if not base or not os.getenv("POLARIS_S3_BUCKET"):
        pytest.skip("POLARIS_BASE_URL / POLARIS_S3_BUCKET not set")
    creds = dh_polaris.env_creds()
    async with dh_polaris.s3_catalog(base, creds, prefix="dh_rtc") as (catalog, namespace):
        yield {
            "catalog": catalog,
            "namespace": namespace,
            "polaris": base,
            "client_id": creds[0],
            "client_secret": creds[1],
        }


@pytest.fixture
def ducklake_meta():
    url = os.getenv("DUCKLAKE_DATABASE_URL")
    if not url:
        pytest.skip("DUCKLAKE_DATABASE_URL not set")
    parts = urlsplit(url)
    return {
        "host": os.getenv("DUCKLAKE_AGENT_HOST", parts.hostname),
        "port": int(os.getenv("DUCKLAKE_AGENT_PORT", parts.port or 5432)),
        "database": parts.path.lstrip("/"),
        "user": parts.username,
        "password": parts.password,
    }


@pytest.mark.parametrize("other", OTHERS)
async def test_iceberg_tables_read_across_runtimes(iceberg_catalog, other):
    _ok(_run(DEFAULT_RUNTIME_ID, "iceberg_write", id=101, label="from-default", **iceberg_catalog))
    rows = _ok(_run(other, "iceberg_read", **iceberg_catalog))["rows"]
    assert [101, "from-default"] in rows

    _ok(_run(other, "iceberg_write", id=102, label="from-other", **iceberg_catalog))
    rows = _ok(_run(DEFAULT_RUNTIME_ID, "iceberg_read", **iceberg_catalog))["rows"]
    assert [101, "from-default"] in rows and [102, "from-other"] in rows


@pytest.mark.parametrize("other", OTHERS)
def test_a_default_runtime_ducklake_catalog_stays_open_to_the_default(
    ducklake_meta, tmp_path, other
):
    lake = {
        "meta": ducklake_meta,
        "schema": f"rtc_{uuid.uuid4().hex[:8]}",
        "data_path": f"{tmp_path}/",
    }
    _ok(_run(DEFAULT_RUNTIME_ID, "ducklake_write", id=1, label="default", **lake))
    _ok(_run(other, "ducklake_write", id=2, label="other", **lake))

    rows = _ok(_run(DEFAULT_RUNTIME_ID, "ducklake_read", **lake))["rows"]
    assert rows == [[1, "default"], [2, "other"]]


@pytest.mark.parametrize("other", OTHERS)
def test_a_catalog_a_newer_runtime_creates_matches_its_declared_format(
    ducklake_meta, tmp_path, other
):
    """The manifest says whether the default can open what this runtime creates;
    this holds the manifest to what the extensions actually do."""
    lake = {
        "meta": ducklake_meta,
        "schema": f"rtc_{uuid.uuid4().hex[:8]}",
        "data_path": f"{tmp_path}/",
    }
    _ok(_run(other, "ducklake_write", id=1, label="other", **lake))

    read = _run(DEFAULT_RUNTIME_ID, "ducklake_read", **lake)
    declared_open = RUNTIMES[other].ducklake_format in RUNTIMES[DEFAULT_RUNTIME_ID].ducklake_formats
    assert read["ok"] is declared_open, read


@pytest.mark.parametrize("other", OTHERS)
@pytest.mark.parametrize("direction", ["default-to-other", "other-to-default"])
def test_result_pages_decode_across_runtimes(tmp_path, other, direction):
    writer, reader = (
        (DEFAULT_RUNTIME_ID, other)
        if direction == "default-to-other"
        else (other, DEFAULT_RUNTIME_ID)
    )
    path = str(tmp_path / "page.parquet")
    _ok(_run(writer, "parquet_write", path=path))

    assert (
        _ok(_run(reader, "parquet_read", path=path))["rows"]
        == _ok(_run(writer, "parquet_read", path=path))["rows"]
    )
