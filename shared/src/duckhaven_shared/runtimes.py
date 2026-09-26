"""The curated agent runtimes: which DuckDB line and extensions each agent image carries.

A runtime is one CI-built agent image, identified by the DuckDB line it pins plus a
fixed extension variant (``1.5``, later e.g. ``2.0`` or ``2.0-geo``). It is a
property of compute: an admin picks one per agent, and auto-provisioned elastic
agents use the deployment's default. Everything that needs to know what a runtime
contains reads it from here — the agent image build, the agent's capability probe,
the API's routing, and CI's build matrix — so the list cannot drift between them.

The exact DuckDB patch is deliberately not recorded. It rides DuckHaven releases as a
maintenance update of the runtime: ``1.5`` means "the newest 1.5.x this release was
built with". The agent reports the patch it actually runs.

Only the extension *install* names live here. What a catalog kind or storage backend
*requires* is a different question, answered by ``api.services.agent_capabilities``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

RuntimeStatus = Literal["beta", "ga", "deprecated", "retired"]


@dataclass(frozen=True)
class Runtime:
    id: str
    display_name: str
    # The engine's `select version()` must be `v<duckdb_line>.<patch>`.
    duckdb_line: str
    # beta: explicit use only, never auto-picked. ga: normal. deprecated: still
    # served, no new compute. retired: refused.
    status: RuntimeStatus
    # Install names baked into the image, in install order. `postgres` reports
    # itself as `postgres_scanner` once loaded; `iceberg` also pulls in `avro`.
    extensions: tuple[str, ...]
    # The DuckLake catalog format this runtime's `ducklake` extension creates a
    # new catalog in, and every format it attaches as-is (without migrating it).
    # Formats migrate one way only, and a newer extension can create a format an
    # older one cannot open — so a runtime may attach a catalog only in a format
    # listed here, and may create one only if the default runtime could open it.
    ducklake_format: str | None
    ducklake_formats: tuple[str, ...]
    # When upstream community support for this DuckDB line ends.
    upstream_eol: date | None


RUNTIMES: dict[str, Runtime] = {
    "1.5": Runtime(
        id="1.5",
        display_name="DuckDB 1.5",
        duckdb_line="1.5",
        status="ga",
        extensions=("httpfs", "azure", "iceberg", "ducklake", "postgres"),
        ducklake_format="1.0",
        ducklake_formats=("1.0",),
        upstream_eol=date(2026, 11, 1),
    ),
    # A pre-release build (see agent/runtimes/2.0.in) until DuckDB 2.0 ships; its
    # qualification record is docs/developer/runtime-qualification.md.
    "2.0": Runtime(
        id="2.0",
        display_name="DuckDB 2.0",
        duckdb_line="2.0",
        status="beta",
        extensions=("httpfs", "azure", "iceberg", "ducklake", "postgres"),
        # Measured: 2.0 opens and writes a 1.0 catalog without migrating it, but a
        # catalog it creates is 1.1-dev1, which 1.5 cannot open.
        ducklake_format="1.1-dev1",
        ducklake_formats=("1.0", "1.1-dev1"),
        upstream_eol=None,
    ),
}

DEFAULT_RUNTIME_ID = "1.5"

# `select version()` gives "v1.5.5"; the Python `duckdb.version()` gives
# "1.5.5 (with duckdb 1.5.5)"; pre-releases look like "v2.0.0-alpha41344".
_LINE = re.compile(r"^v?(\d+)\.(\d+)")


def get(runtime_id: str | None) -> Runtime | None:
    """The runtime with this id, or None if it is not a curated runtime."""
    return RUNTIMES.get(runtime_id) if runtime_id else None


def engine_line(version: str | None) -> str | None:
    """The ``major.minor`` line of a DuckDB version string, in any form DuckDB prints."""
    match = _LINE.match((version or "").strip())
    return f"{match.group(1)}.{match.group(2)}" if match else None


def infer(version: str | None) -> Runtime | None:
    """The base runtime for an engine version, for agents built before runtimes existed.

    Only variant-less runtimes are candidates: a legacy image carries the standard
    extension set, never a variant's extras.
    """
    return get(engine_line(version))
