"""tpch-bench CLI — the entry point that actually wires settings, the
engine clients, the query/DDL corpus, and the orchestrator's ledger/WAL
together into something runnable (plan §1's file layout).

`run`'s methodology-freeze step (`Ledger.register_methodology`, DO NOTHING
on conflict) is what turns "the first real invocation of this CLI" into
"the moment METHODOLOGY.md's hash becomes binding" — see its own §9. There
is no separate freeze command; freezing was never meant to be a step
someone remembers to run before Phase 0, it's what Phase 0 starting *is*.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import typer

from tpch_bench.azure.setup_service_account import bootstrap_service_account
from tpch_bench.clients.base import EngineClient
from tpch_bench.clients.databricks import DatabricksClient
from tpch_bench.clients.duckhaven import DuckHavenClient
from tpch_bench.clients.snowflake import SnowflakeClient
from tpch_bench.ledger.store import Ledger
from tpch_bench.ledger.wal import WalWriter
from tpch_bench.orchestrator import (
    scenario_cold_start,
    scenario_concurrent,
    scenario_dml,
    scenario_sequential,
    scenario_write,
)
from tpch_bench.orchestrator.runner import RunContext
from tpch_bench.settings import (
    Settings,
    engines_config,
    get_settings,
    scale_factors_config,
    scenarios_config,
)

app = typer.Typer(help="First-party TPC-H benchmark: DuckHaven vs Snowflake vs Databricks.")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # benchmarks/tpch/
_QUERIES_DIR = _REPO_ROOT / "queries" / "dialect"
_DDL_DIR = _REPO_ROOT / "ddl"
_METHODOLOGY_PATH = _REPO_ROOT / "METHODOLOGY.md"
_DEFAULT_DB_PATH = _REPO_ROOT / "db" / "results.duckdb"

ENGINES = ("duckhaven", "snowflake", "databricks")
_SEQUENTIAL_LIKE = {"sequential": scenario_sequential, "cold_start": scenario_cold_start}


def load_dialect_queries(engine: str) -> dict[str, str]:
    """{"q01": sql, ...} from queries/dialect/<engine>/qNN.sql."""
    return {p.stem: p.read_text() for p in sorted((_QUERIES_DIR / engine).glob("q*.sql"))}


def load_ddl_by_shape(engine: str) -> dict[str, str]:
    """{"narrow": sql, "wide": sql} from ddl/<engine>/*.sql."""
    return {p.stem: p.read_text() for p in sorted((_DDL_DIR / engine).glob("*.sql"))}


def build_client(engine: str, settings: Settings) -> EngineClient:
    if engine == "duckhaven":
        return DuckHavenClient(
            host=settings.duckhaven_base_url,
            workspace=settings.duckhaven_workspace,
            pat=settings.duckhaven_pat,
        )
    if engine == "snowflake":
        cfg = engines_config()["snowflake"]
        return SnowflakeClient(
            account=settings.snowflake_account,
            user=settings.snowflake_user,
            password=settings.snowflake_password,
            warehouse=settings.snowflake_warehouse,
            database=cfg["database"],
            role=settings.snowflake_role or None,
            application=cfg.get("application", "tpch-bench"),
        )
    if engine == "databricks":
        cfg = engines_config()["databricks"]
        return DatabricksClient(
            server_hostname=settings.databricks_host,
            http_path=f"/sql/1.0/warehouses/{settings.databricks_warehouse_id}",
            client_id=settings.databricks_client_id,
            client_secret=settings.databricks_client_secret,
            catalog=cfg.get("catalog"),
        )
    raise ValueError(f"unknown engine {engine!r}, expected one of {ENGINES}")


def methodology_hash() -> str:
    return hashlib.sha256(_METHODOLOGY_PATH.read_bytes()).hexdigest()


def build_context(
    *,
    ledger: Ledger,
    wal: WalWriter,
    engine: str,
    scale_factor: int,
    run_id: str,
    query_timeout_s: float,
) -> RunContext:
    md_hash = methodology_hash()
    ledger.register_methodology(md_hash, str(_METHODOLOGY_PATH))
    return RunContext(
        ledger=ledger,
        wal=wal,
        engine=engine,
        scale_factor=scale_factor,
        run_id=run_id,
        methodology_hash=md_hash,
        query_timeout_s=query_timeout_s,
    )


@app.command()
def status(db: Path = typer.Option(_DEFAULT_DB_PATH, help="Path to results.duckdb")) -> None:
    """Summarize work-item counts by engine/scale factor/scenario/status."""
    if not db.exists():
        typer.echo(f"No results database at {db} yet.")
        raise typer.Exit(code=0)
    with Ledger(db) as ledger:
        rows = ledger.conn.execute(
            "SELECT engine, scale_factor, scenario, status, count(*) AS n "
            "FROM work_items GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"
        ).fetchall()
    if not rows:
        typer.echo("No work items registered yet.")
        return
    for engine, scale_factor, scenario, item_status, count in rows:
        typer.echo(f"{engine:10} sf{scale_factor:<6} {scenario or '-':12} {item_status:9} {count}")


@app.command("setup-service-account")
def setup_service_account_command(
    admin_email: str = typer.Option(
        ...,
        envvar="DUCKHAVEN_ADMIN_EMAIL",
        help="A local (password-auth) workspace-owner admin, used once to log "
        "in and bootstrap the benchmark's service account. DuckHaven has no "
        "PAT-issuance feature for human users, so this is the only credential "
        "an admin actually has to offer here.",
    ),
    admin_password: str = typer.Option(
        ..., envvar="DUCKHAVEN_ADMIN_PASSWORD", help="Never stored."
    ),
    name: str = typer.Option("tpch-bench"),
    workspace_role: str = typer.Option("writer"),
) -> None:
    """Create the benchmark's DuckHaven service account, grant it workspace
    access, and print its PAT — copy it into .env's DUCKHAVEN_PAT."""
    settings = get_settings()
    result = bootstrap_service_account(
        base_url=settings.duckhaven_base_url,
        workspace=settings.duckhaven_workspace,
        admin_email=admin_email,
        admin_password=admin_password,
        name=name,
        workspace_role=workspace_role,
    )
    typer.echo(f"Service account: {result.service_account_id}")
    typer.echo(f"PAT (shown once — copy into .env's DUCKHAVEN_PAT now): {result.pat}")
    if result.expires_at:
        typer.echo(f"Expires: {result.expires_at}")


@app.command()
def run(
    engine: str = typer.Option(...),
    scale_factor: str = typer.Option(..., help="A config/scale_factors.yaml key, e.g. sf1"),
    scenario: str = typer.Option(...),
    db: Path = typer.Option(_DEFAULT_DB_PATH, help="Path to results.duckdb"),
    runs_dir: Path = typer.Option(_REPO_ROOT / "runs", help="Parent directory for this run's WAL"),
    run_id: str | None = typer.Option(None, help="Defaults to <engine>-<sf>-<scenario>-<epoch>"),
) -> None:
    """Run one (engine, scale_factor, scenario) combination against
    whatever tables are already loaded, resuming from the ledger."""
    if engine not in ENGINES:
        raise typer.BadParameter(f"must be one of {ENGINES}", param_hint="--engine")
    sf_all = scale_factors_config()["scale_factors"]
    if scale_factor not in sf_all:
        raise typer.BadParameter(f"must be one of {sorted(sf_all)}", param_hint="--scale-factor")
    sf_cfg = sf_all[scale_factor]
    if scenario not in sf_cfg["scenarios"]:
        raise typer.BadParameter(
            f"{scenario!r} is not enabled for {scale_factor} (enabled: {sf_cfg['scenarios']})",
            param_hint="--scenario",
        )

    settings = get_settings()
    resolved_run_id = run_id or f"{engine}-{scale_factor}-{scenario}-{int(time.time())}"
    db.parent.mkdir(parents=True, exist_ok=True)
    wal_path = runs_dir / resolved_run_id / "wal.jsonl"

    with Ledger(db) as ledger, WalWriter(wal_path) as wal:
        ctx = build_context(
            ledger=ledger,
            wal=wal,
            engine=engine,
            scale_factor=sf_cfg["factor"],
            run_id=resolved_run_id,
            query_timeout_s=float(sf_cfg["query_timeout_s"]),
        )
        _dispatch(ctx, engine=engine, scenario=scenario, settings=settings)

    typer.echo(f"Done: {engine} {scale_factor} {scenario} (run_id={resolved_run_id})")


def _dispatch(ctx: RunContext, *, engine: str, scenario: str, settings: Settings) -> None:
    scenario_cfg = scenarios_config()["scenarios"][scenario]

    if scenario in _SEQUENTIAL_LIKE:
        client = build_client(engine, settings)
        try:
            queries = load_dialect_queries(engine)
            _SEQUENTIAL_LIKE[scenario].run(ctx, client, queries, reps=scenario_cfg["reps"])
        finally:
            client.close()
        return

    if scenario == "concurrent":
        queries = load_dialect_queries(engine)

        def factory() -> EngineClient:
            return build_client(engine, settings)

        scenario_concurrent.run(ctx, factory, queries, reps=scenario_cfg["reps"])
        return

    if scenario == "write":
        client = build_client(engine, settings)
        try:
            pre_statement = (
                scenario_cfg.get("duckhaven_pre_statement") if engine == "duckhaven" else None
            )
            scenario_write.run(
                ctx,
                client,
                load_ddl_by_shape(engine),
                reps=scenario_cfg["reps"],
                duckhaven_pre_statement=pre_statement,
            )
        finally:
            client.close()
        return

    if scenario == "dml":
        client = build_client(engine, settings)
        try:
            scenario_dml.run(ctx, client, load_ddl_by_shape(engine), cycles=scenario_cfg["cycles"])
        finally:
            client.close()
        return

    raise typer.BadParameter(f"unknown scenario {scenario!r}", param_hint="--scenario")


if __name__ == "__main__":
    app()
