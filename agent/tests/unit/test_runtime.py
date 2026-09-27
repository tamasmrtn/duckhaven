"""The agent's view of its own engine: runtime identity and detected features."""

import json

import duckdb
import pytest

from agent import runtime
from duckhaven_shared import runtimes


def test_engine_version_is_the_engine_spelling():
    assert runtime.ENGINE_VERSION == duckdb.connect().execute("select version()").fetchone()[0]
    assert runtime.ENGINE_VERSION.startswith("v")


def test_a_checkout_infers_its_runtime_from_the_engine_line():
    # No runtime.json outside an image, so the id comes from the DuckDB line.
    assert not runtime.RUNTIME_FILE.exists()
    inferred = runtimes.infer(runtime.ENGINE_VERSION)
    assert runtime.RUNTIME_ID == (inferred.id if inferred else None)
    assert runtime.APP_VERSION is None


def test_the_runtime_file_is_read_when_present(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"runtime_id": "2.0", "app_version": "1.4.0"}))
    assert runtime._read_runtime_file(path) == {"runtime_id": "2.0", "app_version": "1.4.0"}


@pytest.mark.parametrize("content", [None, "not json"])
def test_a_missing_or_corrupt_runtime_file_is_ignored(tmp_path, content):
    path = tmp_path / "runtime.json"
    if content is not None:
        path.write_text(content)
    assert runtime._read_runtime_file(path) == {}


def test_profile_setting_is_one_this_engine_has():
    assert runtime.PROFILE_SETTING in runtime.SETTINGS


def test_secret_bind_parameters_are_detected_on_this_engine():
    """Detected, not assumed: the probe must agree with what the engine does."""
    conn = duckdb.connect()
    # INSTALL as well: a fresh machine (CI) has no extensions cached.
    conn.execute("INSTALL httpfs")
    conn.execute("LOAD httpfs")
    try:
        conn.execute("CREATE TEMPORARY SECRET t (TYPE HTTP, BEARER_TOKEN ?)", ["x"])
        takes_parameters = True
    except duckdb.NotImplementedException:
        takes_parameters = False
    assert runtime.SECRET_BIND_PARAMETERS is takes_parameters


def test_the_bind_parameter_probe_reads_the_2_0_refusal():
    class Refusing:
        def execute(self, sql, params=None):
            raise duckdb.NotImplementedException(
                "Not implemented Error: Unrecognized expression type PARAMETER"
            )

    class Accepting:
        def execute(self, sql, params=None):
            raise duckdb.InvalidInputException("Secret type 'dh_no_such_type' not found")

    assert runtime._secret_takes_bind_parameters(Refusing()) is False
    assert runtime._secret_takes_bind_parameters(Accepting()) is True
