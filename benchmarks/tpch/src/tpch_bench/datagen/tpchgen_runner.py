"""Wraps the `tpchgen-cli` binary (PyPI: `tpchgen-cli`, a compiled Rust
TPC-H generator — https://github.com/clflushopt/tpchgen-rs), chosen over
the reference `dbgen` for the generation speed SF100-SF1000 needs (plan
§5/§6). Shells out to the real tool rather than reimplementing TPC-H's
data generation, the same stance as `clients/duckhaven.py` wrapping the
real connector instead of hand-rolling one.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TABLES: tuple[str, ...] = (
    "region",
    "nation",
    "supplier",
    "customer",
    "part",
    "partsupp",
    "orders",
    "lineitem",
)


class TpchgenNotFoundError(RuntimeError):
    """`tpchgen-cli` isn't on PATH — it's a project dependency; run `uv sync`."""


class TpchgenGenerationError(RuntimeError):
    """`tpchgen-cli` exited non-zero, or didn't produce an expected file."""


@dataclass(frozen=True)
class GeneratedTable:
    table: str
    files: list[Path]


def _binary_path() -> str:
    binary = shutil.which("tpchgen-cli")
    if binary is None:
        raise TpchgenNotFoundError(
            "tpchgen-cli not found on PATH; it's a benchmarks/tpch dependency "
            "(pyproject.toml) — run `uv sync` in benchmarks/tpch"
        )
    return binary


def generate(
    *,
    scale_factor: float,
    output_dir: Path,
    tables: tuple[str, ...] = TABLES,
    parts: int | None = None,
    compression: str = "SNAPPY",
    num_threads: int | None = None,
) -> list[GeneratedTable]:
    """Generate `tables` at `scale_factor` as Parquet into `output_dir`.

    One generation pass; the caller decides whether to skip it (the corpus
    manager, `datagen/corpus.py`, only calls this when its manifest shows
    no checksum-verified copy already exists for this scale factor).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    args = [
        _binary_path(),
        "parquet",
        "-s",
        str(scale_factor),
        "--output-dir",
        str(output_dir),
        "--tables",
        ",".join(tables),
        "--compression",
        compression,
        "--quiet",
        "--no-progress",
    ]
    if parts is not None:
        args += ["--parts", str(parts)]
    if num_threads is not None:
        args += ["--num-threads", str(num_threads)]

    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise TpchgenGenerationError(
            f"tpchgen-cli failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc

    results = []
    for table in tables:
        if parts is None:
            files = [output_dir / f"{table}.parquet"]
        else:
            files = sorted((output_dir / table).glob(f"{table}.*.parquet"))
        missing = [f for f in files if not f.exists()]
        if missing:
            raise TpchgenGenerationError(
                f"tpchgen-cli reported success but did not produce: {missing}"
            )
        results.append(GeneratedTable(table=table, files=files))
    return results
