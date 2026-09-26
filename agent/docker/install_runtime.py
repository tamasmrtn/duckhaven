"""Bake one runtime's DuckDB extensions into the image and record its identity.

Run once in the builder stage of agent/Dockerfile:

    python agent/docker/install_runtime.py <runtime id> <app version> <runtime.json path>

It fails the build rather than producing an image that builds cleanly but cannot
serve a catalog: extension binaries are published per exact DuckDB build, so a DuckDB
whose extensions aren't published yet (or a runtime that pulled the wrong DuckDB)
must stop here, not at the first query.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

from duckhaven_shared import runtimes


def main(runtime_id: str, app_version: str, out: Path) -> None:
    runtime = runtimes.get(runtime_id)
    if runtime is None:
        sys.exit(f"Unknown runtime {runtime_id!r}; known: {', '.join(runtimes.RUNTIMES)}")
    if runtime.status == "retired":
        sys.exit(f"Runtime {runtime_id!r} is retired and is no longer built")

    conn = duckdb.connect()
    engine = conn.execute("select version()").fetchone()[0]
    if runtimes.engine_line(engine) != runtime.duckdb_line:
        sys.exit(
            f"Runtime {runtime_id!r} needs DuckDB {runtime.duckdb_line}.x but the image has "
            f"{engine}; is agent/runtimes/{runtime_id}.txt missing?"
        )
    for name in runtime.extensions:
        conn.execute(f"INSTALL {name}")
        # LOAD raises when the binary is missing or built for another DuckDB.
        conn.execute(f"LOAD {name}")

    out.write_text(json.dumps({"runtime_id": runtime.id, "app_version": app_version}))
    print(f"runtime {runtime.id}: duckdb {engine}, extensions {', '.join(runtime.extensions)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
