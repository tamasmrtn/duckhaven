"""One side of a cross-runtime check, run under one runtime's interpreter.

    <runtime venv>/bin/python tests/runtime_compat/worker.py <action> '<json args>'

Prints one JSON object: the engine version plus the action's result. Attaches
catalogs through the agent's own runner, so what is exercised is what an agent on
that runtime would do, not a hand-written approximation of it.
"""

from __future__ import annotations

import json
import sys

import duckdb

from agent.executor import runner


def _iceberg(args: dict) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    # INSTALL too: a fresh CI machine has no extensions cached for this line.
    for ext in ("httpfs", "iceberg"):
        conn.execute(f"INSTALL {ext}")
        conn.execute(f"LOAD {ext}")
    runner._attach_catalogs(
        conn,
        catalogs=[
            {
                "slug": args["catalog"],
                "polaris_name": args["catalog"],
                "backend": {"kind": "s3"},
                "default_schema": args["namespace"],
            }
        ],
        active_catalog=args["catalog"],
        polaris={
            "endpoint": args["polaris"],
            "client_id": args["client_id"],
            "client_secret": args["client_secret"],
        },
    )
    runner._apply_sandbox(conn, None, lock_config=True)
    return conn


def _ducklake(args: dict) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    for ext in ("ducklake", "postgres"):
        conn.execute(f"INSTALL {ext}")
        conn.execute(f"LOAD {ext}")
    runner._attach_ducklake(
        conn,
        {
            "slug": "lake",
            "kind": "ducklake",
            "data_path": args["data_path"],
            "metadata_schema": args["schema"],
            "default_schema": "main",
            "meta": args["meta"],
        },
    )
    # Locked as an agent locks it: DuckDB 2.0's `ducklake` changes a setting the
    # lock refused until the agent allowed it, which only showed up this way.
    runner._apply_sandbox(conn, None, lock_config=True)
    return conn


def iceberg_write(args: dict) -> dict:
    conn = _iceberg(args)
    conn.execute(f"INSERT INTO events VALUES ({args['id']}, '{args['label']}')")
    return {"count": conn.execute("SELECT count(*) FROM events").fetchone()[0]}


def iceberg_read(args: dict) -> dict:
    conn = _iceberg(args)
    rows = conn.execute("SELECT * FROM events ORDER BY 1").fetchall()
    return {"rows": [list(r) for r in rows]}


def ducklake_write(args: dict) -> dict:
    conn = _ducklake(args)
    conn.execute('CREATE TABLE IF NOT EXISTS "lake"."main".t (id INTEGER, label VARCHAR)')
    conn.execute(f'INSERT INTO "lake"."main".t VALUES ({args["id"]}, \'{args["label"]}\')')
    return {"count": conn.execute('SELECT count(*) FROM "lake"."main".t').fetchone()[0]}


def ducklake_read(args: dict) -> dict:
    conn = _ducklake(args)
    rows = conn.execute('SELECT * FROM "lake"."main".t ORDER BY 1').fetchall()
    return {"rows": [list(r) for r in rows]}


# Types the agent materializes into result pages, including the ones its writer
# is known to widen, so a reader on another line decodes what this line wrote.
_RESULT_SQL = (
    "SELECT 1::HUGEINT AS big, 'x'::VARCHAR AS s, [1, 2]::INTEGER[] AS arr, "
    "{'a': 1} AS st, DATE '2026-01-02' AS d, TIMESTAMPTZ '2026-01-02 03:04:05+00' AS ts, "
    "1.25::DECIMAL(18, 3) AS dec, NULL::DOUBLE AS n"
)


def parquet_write(args: dict) -> dict:
    duckdb.connect().sql(_RESULT_SQL).write_parquet(args["path"])
    return {}


def parquet_read(args: dict) -> dict:
    rows = duckdb.connect().execute(f"SELECT * FROM read_parquet('{args['path']}')").fetchall()
    return {"rows": [[str(v) for v in r] for r in rows]}


if __name__ == "__main__":
    action, raw = sys.argv[1], sys.argv[2]
    try:
        result = globals()[action](json.loads(raw))
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - the caller asserts on the failure
        result = {"ok": False, "error": str(exc).splitlines()[0]}
    result["engine"] = duckdb.connect().execute("select version()").fetchone()[0]
    print(json.dumps(result))
