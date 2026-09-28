"""The runtime manifest parses every DuckDB version spelling and stays self-consistent."""

import pytest

from duckhaven_shared import runtimes


@pytest.mark.parametrize(
    ("version", "line"),
    [
        ("v1.5.5", "1.5"),
        ("1.5.5 (with duckdb 1.5.5)", "1.5"),
        ("v2.0.0-alpha41344", "2.0"),
        ("1.6.0.dev365", "1.6"),
        ("  v10.12.0 ", "10.12"),
        ("", None),
        (None, None),
        ("duckdb", None),
    ],
)
def test_engine_line(version, line):
    assert runtimes.engine_line(version) == line


def test_infer_maps_a_known_line_to_its_runtime():
    assert runtimes.infer("1.5.5 (with duckdb 1.5.5)") is runtimes.RUNTIMES["1.5"]
    assert runtimes.infer("v1.4.3") is None


def test_get_ignores_unknown_and_empty_ids():
    assert runtimes.get("1.5") is runtimes.RUNTIMES["1.5"]
    assert runtimes.get("9.9") is None
    assert runtimes.get(None) is None


def test_default_is_a_curated_generally_available_runtime():
    default = runtimes.RUNTIMES[runtimes.DEFAULT_RUNTIME_ID]
    assert default.status == "ga"


@pytest.mark.parametrize("runtime", list(runtimes.RUNTIMES.values()), ids=lambda r: r.id)
def test_every_runtime_is_keyed_by_its_own_line(runtime):
    assert runtime.id.split("-")[0] == runtime.duckdb_line
    assert runtime.extensions


@pytest.mark.parametrize("runtime", list(runtimes.RUNTIMES.values()), ids=lambda r: r.id)
def test_every_runtime_opens_the_ducklake_format_it_creates(runtime):
    assert runtime.ducklake_format in runtime.ducklake_formats


@pytest.mark.parametrize("runtime", list(runtimes.RUNTIMES.values()), ids=lambda r: r.id)
def test_every_runtime_opens_the_default_runtimes_catalogs(runtime):
    """Otherwise it could never serve a catalog the default runtime created."""
    default = runtimes.RUNTIMES[runtimes.DEFAULT_RUNTIME_ID]
    assert default.ducklake_format in runtime.ducklake_formats
