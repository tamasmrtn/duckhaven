"""What this agent's DuckDB engine is, and which of its optional behaviours it has.

Probed once, at import, on a scratch connection. Everything version-dependent in the
agent reads from here, and prefers **detecting a feature** to comparing version
numbers: the DuckDB 2.0 previews shipped on PyPI as ``1.6.0.devN`` while the engine
reported ``v2.0.0-alphaN``, so a packaging version cannot be trusted to pick
behaviour, and a present-or-absent setting is exactly what the code needs to know.

The runtime id comes from ``runtime.json``, which the image build writes (see
agent/docker/install_runtime.py). It is baked into the image rather than read from
the environment, so an operator's ``ELASTIC_AGENT_ENV`` cannot make an agent claim a
runtime it isn't. A checkout or a pre-runtime image has no such file, so the id is
inferred from the engine's DuckDB line.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb

from duckhaven_shared import runtimes

logger = logging.getLogger(__name__)

RUNTIME_FILE = Path("/app/runtime.json")


def _read_runtime_file(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable %s: %s", path, exc)
        return {}


def _secret_takes_bind_parameters(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether `CREATE SECRET` accepts `?` parameters for its option values.

    DuckDB 1.5 does; 2.0 refuses them ("Unrecognized expression type PARAMETER")
    while parsing, before it resolves the secret type. So a probe naming a type
    that doesn't exist tells the two apart without creating anything or needing
    an extension: 1.5 gets past the parameter and fails on the type instead.
    """
    try:
        conn.execute("CREATE TEMPORARY SECRET dh_probe (TYPE dh_no_such_type, PROBE ?)", ["x"])
    except duckdb.NotImplementedException as exc:
        if "PARAMETER" in str(exc):
            return False
    except duckdb.Error:
        pass
    return True


with duckdb.connect() as _conn:
    # `select version()` is "v1.5.5"; `duckdb.version()` is the Python client's
    # "1.5.5 (with duckdb 1.5.5)", which the capabilities keep reporting as-is.
    ENGINE_VERSION: str = _conn.execute("select version()").fetchone()[0]
    PLATFORM: str = _conn.execute("pragma platform").fetchone()[0]
    SETTINGS: frozenset[str] = frozenset(
        row[0] for row in _conn.execute("select name from duckdb_settings()").fetchall()
    )
    SECRET_BIND_PARAMETERS: bool = _secret_takes_bind_parameters(_conn)

_identity = _read_runtime_file(RUNTIME_FILE)
_inferred = runtimes.infer(ENGINE_VERSION)
RUNTIME_ID: str | None = _identity.get("runtime_id") or (_inferred.id if _inferred else None)
APP_VERSION: str | None = _identity.get("app_version")

# DuckDB 2.0 removed `custom_profiling_settings` in favour of `tracked_metrics`,
# which takes glob patterns rather than metric names. Each build rejects the
# other's option, so this picks whichever one the engine actually has.
PROFILE_SETTING: str = (
    "tracked_metrics" if "tracked_metrics" in SETTINGS else "custom_profiling_settings"
)
