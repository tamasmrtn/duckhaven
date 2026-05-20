import time
from pathlib import Path

import duckdb


def run_query_sync(
    sql: str,
    result_path: Path,
    memory_limit_gb: float,
) -> dict[str, int]:
    conn = duckdb.connect()
    conn.execute(f"SET memory_limit='{memory_limit_gb}GB'")
    start = time.monotonic()
    conn.execute(f"COPY ({sql}) TO '{result_path}' (FORMAT PARQUET)")
    duration_ms = int((time.monotonic() - start) * 1000)
    row_count_result = conn.execute(
        f"SELECT count(*) FROM read_parquet('{result_path}')"
    ).fetchone()
    row_count = row_count_result[0] if row_count_result else 0
    conn.close()
    return {"row_count": row_count, "duration_ms": duration_ms}
