"""Agent-backed DuckDB SQL metadata for editor autocomplete.

Runs DuckDB's meta table-functions (`duckdb_functions()`, `duckdb_keywords()`,
`duckdb_types()`) on a connected agent and shapes the rows into the
function/keyword/type dictionary the worksheet editor's IntelliSense consumes.

The result is static per DuckDB version, so it is cached in-process keyed by the
agent's reported version (cleared on restart — no TTL needed).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.workspace import Workspace
from api.schemas.query import SqlFunctionOut, SqlKeywordOut, SqlMetadataOut, SqlTypeOut
from api.services import query as query_service

# List columns are flattened to scalars in SQL so the Parquet decode stays simple
# (no nested-type handling), and overloads are collapsed to one row per function.
_FUNCTIONS_SQL = """
SELECT function_name,
       any_value(function_type)                          AS function_type,
       any_value(return_type)                            AS return_type,
       any_value(array_to_string(parameters, ', '))      AS parameters,
       any_value(array_to_string(parameter_types, ', ')) AS parameter_types,
       any_value(varargs)                                AS varargs,
       any_value(array_to_string(examples, ' | '))       AS examples
FROM duckdb_functions()
WHERE function_type IN ('scalar', 'aggregate', 'macro')
  -- Drop operator functions exposed under symbolic names (!, !~~, ||, @, …);
  -- keep only callable identifiers a user would actually type.
  AND regexp_full_match(function_name, '[a-zA-Z_][a-zA-Z0-9_]*')
GROUP BY function_name
ORDER BY function_name
"""

_KEYWORDS_SQL = "SELECT keyword_name, keyword_category FROM duckdb_keywords()"

_TYPES_SQL = (
    "SELECT DISTINCT type_name, type_category FROM duckdb_types() "
    "WHERE type_name IS NOT NULL ORDER BY type_name"
)

_cache: dict[str, SqlMetadataOut] = {}


def build_signature(
    name: str,
    parameters: str | None,
    parameter_types: str | None,
    varargs: str | None,
    return_type: str | None,
) -> str:
    """A human-readable ``fn(a, b, ...) → type`` signature.

    Pairs parameter names with their types when both are present and aligned,
    otherwise falls back to whichever the function exposes.
    """
    names = [p for p in (parameters or "").split(", ") if p]
    types = [t for t in (parameter_types or "").split(", ") if t]
    if names and types and len(names) == len(types):
        parts = [f"{n} {t}" for n, t in zip(names, types, strict=True)]
    elif names:
        parts = names
    else:
        parts = types
    if varargs:
        parts.append(f"{varargs}...")
    sig = f"{name}({', '.join(parts)})"
    if return_type:
        sig += f" → {return_type}"
    return sig


def functions_from_rows(rows: list[dict[str, Any]]) -> list[SqlFunctionOut]:
    return [
        SqlFunctionOut(
            name=r["function_name"],
            type=r["function_type"],
            return_type=r.get("return_type"),
            signature=build_signature(
                r["function_name"],
                r.get("parameters"),
                r.get("parameter_types"),
                r.get("varargs"),
                r.get("return_type"),
            ),
            examples=r.get("examples") or None,
        )
        for r in rows
    ]


def keywords_from_rows(rows: list[dict[str, Any]]) -> list[SqlKeywordOut]:
    return [SqlKeywordOut(name=r["keyword_name"], category=r.get("keyword_category")) for r in rows]


def types_from_rows(rows: list[dict[str, Any]]) -> list[SqlTypeOut]:
    return [SqlTypeOut(name=r["type_name"], category=r.get("type_category")) for r in rows]


async def _run_meta_rows(
    db: AsyncSession, workspace: Workspace, agent: Agent, user_id: Any, sql: str
) -> list[dict[str, Any]]:
    """Run a metadata SELECT on the agent and decode all of its result rows."""
    query = await query_service.run_sync_query(
        db,
        workspace=workspace,
        agent=agent,
        user_id=user_id,
        sql=sql,
        origin="metadata",
    )
    if query.status != "done" or query.result_path is None:
        raise RuntimeError("metadata query did not complete")
    token = await query_service.agent_session_token(db, agent.id)
    upstream = await query_service.proxy_rows(agent, query, token=token)
    if upstream.status_code != 200:
        raise RuntimeError("failed to fetch metadata rows from agent")
    limit = query.row_count or 100_000
    rows, _ = query_service.decode_parquet_page(upstream.content, limit, 0)
    return rows


async def fetch_metadata(
    db: AsyncSession, workspace: Workspace, agent: Agent, user_id: Any
) -> SqlMetadataOut:
    """Function/keyword/type dictionary for the agent's DuckDB, cached by version."""
    version = (agent.capabilities or {}).get("duckdb_version")
    if version and version in _cache:
        return _cache[version]

    functions = functions_from_rows(
        await _run_meta_rows(db, workspace, agent, user_id, _FUNCTIONS_SQL)
    )
    keywords = keywords_from_rows(
        await _run_meta_rows(db, workspace, agent, user_id, _KEYWORDS_SQL)
    )
    types = types_from_rows(await _run_meta_rows(db, workspace, agent, user_id, _TYPES_SQL))
    metadata = SqlMetadataOut(functions=functions, keywords=keywords, types=types)
    if version:
        _cache[version] = metadata
    return metadata
