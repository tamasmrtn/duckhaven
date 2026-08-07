import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "queries" / "dialect" / "render_dialects.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("render_dialects", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    # dataclasses needs the module registered before exec: it resolves
    # annotations by looking the module up in sys.modules by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_overrides_declared_yet():
    # See render_dialects.py's module docstring: overrides are added only
    # once a real incompatibility is observed in Phase 0, never up front.
    module = _load_module()
    assert module.OVERRIDES == ()


def test_render_copies_every_canonical_query_to_every_engine(tmp_path, monkeypatch):
    module = _load_module()
    canonical_dir = tmp_path / "tpch_canonical"
    dialect_dir = tmp_path / "dialect"
    canonical_dir.mkdir()
    dialect_dir.mkdir()
    (canonical_dir / "q01.sql").write_text("SELECT 1;\n")
    (canonical_dir / "q02.sql").write_text("SELECT 2;\n")

    monkeypatch.setattr(module, "_CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(module, "_DIALECT_DIR", dialect_dir)
    monkeypatch.setattr(module, "OVERRIDES", ())

    changed = module.render()

    assert changed == {"duckhaven": [], "snowflake": [], "databricks": []}
    for engine in ("duckhaven", "snowflake", "databricks"):
        assert (dialect_dir / engine / "q01.sql").read_text() == "SELECT 1;\n"
        assert (dialect_dir / engine / "q02.sql").read_text() == "SELECT 2;\n"


def test_render_applies_a_declared_override_and_records_it(tmp_path, monkeypatch):
    module = _load_module()
    canonical_dir = tmp_path / "tpch_canonical"
    dialect_dir = tmp_path / "dialect"
    canonical_dir.mkdir()
    dialect_dir.mkdir()
    (canonical_dir / "q01.sql").write_text("SELECT TOP 1 * FROM t;\n")

    override = module.Override(
        engine="snowflake",
        query_nr="01",
        find="TOP 1 *",
        replace="*",
        reason="Snowflake has no TOP syntax; use LIMIT instead (not present here).",
    )
    monkeypatch.setattr(module, "_CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(module, "_DIALECT_DIR", dialect_dir)
    monkeypatch.setattr(module, "OVERRIDES", (override,))

    changed = module.render()

    assert changed["snowflake"] == ["01"]
    assert changed["duckhaven"] == []
    assert (dialect_dir / "snowflake" / "q01.sql").read_text() == "SELECT * FROM t;\n"
    assert (dialect_dir / "duckhaven" / "q01.sql").read_text() == "SELECT TOP 1 * FROM t;\n"

    md = module.diffs_md(changed)
    assert "snowflake" in md
    assert "Q01" in md
    assert override.reason in md


def test_render_raises_on_a_stale_override(tmp_path, monkeypatch):
    module = _load_module()
    canonical_dir = tmp_path / "tpch_canonical"
    dialect_dir = tmp_path / "dialect"
    canonical_dir.mkdir()
    dialect_dir.mkdir()
    (canonical_dir / "q01.sql").write_text("SELECT * FROM t;\n")

    stale_override = module.Override(
        engine="snowflake",
        query_nr="01",
        find="TOP 1 *",
        replace="*",
        reason="no longer matches the canonical text",
    )
    monkeypatch.setattr(module, "_CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(module, "_DIALECT_DIR", dialect_dir)
    monkeypatch.setattr(module, "OVERRIDES", (stale_override,))

    with pytest.raises(ValueError, match="stale override"):
        module.render()


def test_diffs_md_reports_no_overrides_when_none_declared():
    module = _load_module()
    md = module.diffs_md({"duckhaven": [], "snowflake": [], "databricks": []})
    assert "No overrides are declared" in md


def test_the_committed_canonical_corpus_has_all_22_queries():
    canonical_dir = _MODULE_PATH.parents[1] / "tpch_canonical"
    files = sorted(canonical_dir.glob("q*.sql"))
    assert [f.stem for f in files] == [f"q{i:02d}" for i in range(1, 23)]
    for f in files:
        assert f.read_text().strip().endswith(";")


def test_the_committed_dialect_output_matches_the_canonical_corpus():
    # Guards against a stale, committed dialect/ tree drifting from a
    # regenerated tpch_canonical/ — regenerate via render_dialects.py if
    # this fails, don't hand-edit the dialect files.
    module = _load_module()
    changed = module.render()
    assert changed == {"duckhaven": [], "snowflake": [], "databricks": []}
