from __future__ import annotations

import pytest
from typer.testing import CliRunner

from tpch_bench import cli
from tpch_bench.clients.base import EngineClient, QueryResult
from tpch_bench.ledger.store import Ledger
from tpch_bench.settings import get_settings

runner = CliRunner()


class FakeEngineClient(EngineClient):
    def __init__(self) -> None:
        self.connect_calls = 0
        self.close_calls = 0
        self.executed: list[str] = []

    def connect(self) -> None:
        self.connect_calls += 1

    def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
        self.executed.append(sql)
        return QueryResult(client_wall_ms=1.0, row_count=1)

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run_args(tmp_path, **overrides):
    args = {
        "engine": "duckhaven",
        "scale-factor": "sf1",
        "scenario": "sequential",
        "db": str(tmp_path / "r.duckdb"),
        "runs-dir": str(tmp_path / "runs"),
    }
    args.update(overrides)
    out = []
    for key, value in args.items():
        out += [f"--{key}", value]
    return out


# ── status ────────────────────────────────────────────────────────────────


def test_status_with_no_db_reports_and_exits_cleanly(tmp_path):
    result = runner.invoke(cli.app, ["status", "--db", str(tmp_path / "missing.duckdb")])

    assert result.exit_code == 0
    assert "No results database" in result.stdout


def test_status_summarizes_work_items_by_status(tmp_path):
    db = tmp_path / "results.duckdb"
    with Ledger(db) as ledger:
        ledger.register_work_item(
            work_item_id="w1",
            kind="query",
            engine="duckhaven",
            scale_factor=1,
            scenario="sequential",
            query_id="q01",
            rep=0,
        )

    result = runner.invoke(cli.app, ["status", "--db", str(db)])

    assert result.exit_code == 0
    assert "duckhaven" in result.stdout
    assert "pending" in result.stdout


# ── run: validation ──────────────────────────────────────────────────────


def test_run_rejects_an_unknown_engine(tmp_path):
    result = runner.invoke(cli.app, ["run", *_run_args(tmp_path, engine="bogus")])

    assert result.exit_code == 2
    assert "--engine" in result.output


def test_run_rejects_an_unknown_scale_factor(tmp_path):
    result = runner.invoke(cli.app, ["run", *_run_args(tmp_path, **{"scale-factor": "sf9999"})])

    assert result.exit_code == 2
    assert "--scale-factor" in result.output


def test_run_rejects_a_scenario_not_enabled_for_the_scale_factor(tmp_path):
    # config/scale_factors.yaml: sf1000 enables only sequential + cold_start.
    args = _run_args(tmp_path, **{"scale-factor": "sf1000", "scenario": "concurrent"})

    result = runner.invoke(cli.app, ["run", *args])

    assert result.exit_code == 2
    assert "--scenario" in result.output


# ── run: dispatch (build_client patched — no real engine needed) ─────────


def test_run_sequential_connects_once_and_runs_every_query_times_reps(tmp_path, monkeypatch):
    fake = FakeEngineClient()
    monkeypatch.setattr(cli, "build_client", lambda engine, settings: fake)

    result = runner.invoke(cli.app, ["run", *_run_args(tmp_path)])

    assert result.exit_code == 0, result.output
    assert fake.connect_calls == 1
    assert fake.close_calls == 1
    assert len(fake.executed) == 22 * 5  # 22 queries, sequential's 5 reps


def test_run_cold_start_reconnects_once_per_query(tmp_path, monkeypatch):
    fake = FakeEngineClient()
    monkeypatch.setattr(cli, "build_client", lambda engine, settings: fake)

    result = runner.invoke(cli.app, ["run", *_run_args(tmp_path, scenario="cold_start")])

    assert result.exit_code == 0, result.output
    assert fake.connect_calls == 22 * 3  # cold_start's 3 reps
    # One close before each of the 66 queries (the scenario's own
    # close-then-reconnect cycle) plus a final close from run()'s cleanup —
    # the loop ends on an *open* connection, so that last close is the only
    # thing that actually closes it once the command finishes.
    assert fake.close_calls == 22 * 3 + 1


def test_run_concurrent_gives_each_worker_its_own_client(tmp_path, monkeypatch):
    created: list[FakeEngineClient] = []

    def fake_build(engine, settings):
        client = FakeEngineClient()
        created.append(client)
        return client

    monkeypatch.setattr(cli, "build_client", fake_build)

    result = runner.invoke(cli.app, ["run", *_run_args(tmp_path, scenario="concurrent")])

    assert result.exit_code == 0, result.output
    assert len(created) == 22 * 3  # concurrent's 3 reps
    for client in created:
        assert client.connect_calls == 1
        assert client.close_calls == 1


def test_run_write_issues_the_duckhaven_pre_statement_for_duckhaven_only(tmp_path, monkeypatch):
    fake = FakeEngineClient()
    monkeypatch.setattr(cli, "build_client", lambda engine, settings: fake)

    result = runner.invoke(cli.app, ["run", *_run_args(tmp_path, scenario="write")])

    assert result.exit_code == 0, result.output
    pre_statements = [sql for sql in fake.executed if "duckhaven_concurrency" in sql]
    assert len(pre_statements) == 2 * 3  # narrow+wide shapes, write's 3 reps


def test_run_dml_runs_delete_then_insert_per_shape_per_cycle(tmp_path, monkeypatch):
    fake = FakeEngineClient()
    monkeypatch.setattr(cli, "build_client", lambda engine, settings: fake)

    result = runner.invoke(cli.app, ["run", *_run_args(tmp_path, scenario="dml")])

    assert result.exit_code == 0, result.output
    deletes = [sql for sql in fake.executed if sql.startswith("DELETE")]
    inserts = [sql for sql in fake.executed if sql.startswith("INSERT")]
    assert len(deletes) == 2 * 3  # narrow+wide shapes, dml's 3 cycles
    assert len(inserts) == 2 * 3


def test_run_freezes_the_methodology_on_first_use(tmp_path, monkeypatch):
    fake = FakeEngineClient()
    monkeypatch.setattr(cli, "build_client", lambda engine, settings: fake)
    db = tmp_path / "r.duckdb"

    runner.invoke(cli.app, ["run", *_run_args(tmp_path, db=str(db))])

    with Ledger(db) as ledger:
        assert ledger.is_methodology_frozen(cli.methodology_hash())


def test_run_is_resumable_across_two_invocations(tmp_path, monkeypatch):
    created: list[FakeEngineClient] = []

    def fake_build(engine, settings):
        client = FakeEngineClient()
        created.append(client)
        return client

    monkeypatch.setattr(cli, "build_client", fake_build)
    args = _run_args(tmp_path)

    first = runner.invoke(cli.app, ["run", *args])
    assert first.exit_code == 0, first.output
    assert len(created[0].executed) == 22 * 5

    second = runner.invoke(cli.app, ["run", *args])
    assert second.exit_code == 0, second.output
    # build_client may be called again (constructing one is free — no
    # network call happens until .connect()), but everything from the
    # first invocation is already `done`, so the second call's client must
    # never actually be used.
    assert created[-1].connect_calls == 0
    assert created[-1].executed == []


# ── setup-service-account ───────────────────────────────────────────────


def test_setup_service_account_prints_the_pat(monkeypatch):
    from tpch_bench.azure.setup_service_account import ServiceAccountBootstrap

    captured = {}

    def fake_bootstrap(*, base_url, workspace, admin_pat, name, workspace_role):
        captured.update(
            base_url=base_url,
            workspace=workspace,
            admin_pat=admin_pat,
            name=name,
            workspace_role=workspace_role,
        )
        return ServiceAccountBootstrap(
            service_account_id="sa-1", pat="dh_pat_xyz", expires_at="2026-12-01T00:00:00Z"
        )

    monkeypatch.setattr(cli, "bootstrap_service_account", fake_bootstrap)
    monkeypatch.setenv("DUCKHAVEN_BASE_URL", "https://dh.example.com")
    monkeypatch.setenv("DUCKHAVEN_WORKSPACE", "tpch-bench")

    result = runner.invoke(cli.app, ["setup-service-account", "--admin-pat", "dh_pat_admin"])

    assert result.exit_code == 0, result.output
    assert "dh_pat_xyz" in result.output
    assert captured == {
        "base_url": "https://dh.example.com",
        "workspace": "tpch-bench",
        "admin_pat": "dh_pat_admin",
        "name": "tpch-bench",
        "workspace_role": "writer",
    }


def test_setup_service_account_requires_an_admin_pat():
    result = runner.invoke(cli.app, ["setup-service-account"])

    assert result.exit_code != 0
