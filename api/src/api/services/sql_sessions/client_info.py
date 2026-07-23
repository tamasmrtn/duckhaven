"""Identify the tool that opened a SQL session, from its ``User-Agent``.

Attribution's minimal, zero-contract form: clients are expected to lead their
``User-Agent`` with the calling application, ``<product>/<version>`` (e.g.
``dbt-duckhaven/0.1.0`` / ``dlt-duckhaven/0.2.0``), so the API can record *what
workload this is* without adding a request field the published client would have to
learn. Same idea as Postgres' ``application_name`` (set once, on connect) and
Databricks' ``system.query.history.client_application``.

The connector leads with the application from ``duckhaven-sql-connector`` 0.3.0. A
connector older than that — or one opened with no ``application=`` set — leads with
its own token and is recorded as ``duckhaven-sql-connector``, so the workload is not
distinguished. ``sql_sessions`` rows written before that client fix carry that value,
and audit history will show it.

Deliberately not a general User-Agent parser: DuckHaven's clients emit a fixed
``product/version`` shape, and a browser's sprawling UA string is not something the
session audit needs to understand. Anything that does not match that shape yields
``(None, None)`` rather than a guess.
"""

from __future__ import annotations

# Match the model's column widths; a longer value is truncated, never rejected —
# a weird UA must not fail a session open.
_MAX_NAME = 64
_MAX_VERSION = 32


def parse_user_agent(user_agent: str | None) -> tuple[str | None, str | None]:
    """Split a ``User-Agent`` into ``(client_name, client_version)``.

    Reads the first whitespace-separated product token, e.g.
    ``"dbt-duckhaven/1.2.0 (linux)"`` -> ``("dbt-duckhaven", "1.2.0")``. A token
    with no ``/`` yields a name and no version (``"curl"`` -> ``("curl", None)``).
    """
    if not user_agent:
        return None, None
    token = user_agent.strip().split(" ", 1)[0]
    if not token:
        return None, None
    name, _, version = token.partition("/")
    name = name.strip()
    version = version.strip()
    if not name:
        return None, None
    return name[:_MAX_NAME], version[:_MAX_VERSION] or None
