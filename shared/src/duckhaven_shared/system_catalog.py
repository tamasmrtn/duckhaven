"""Names the control plane and the agent agree on for the ``duckhaven`` system catalog.

The control plane sends it as one more catalog to attach, of kind ``system``,
only for a statement that reads it and only to an agent advertising the
``system_catalog`` and ``system_catalog_row_filter`` protocol features. The agent
attaches it read-only through the ``postgres`` extension and never makes it the
current catalog.

Its database filters rows by who is asking: the descriptor carries settings that
the agent passes to Postgres in the connection string (``options``), and the
store's row-level security reads them. An agent that attached without them would
read nothing, which is why the control plane sends the catalog only to an agent
that says it passes them.
"""

import re

KIND_SYSTEM = "system"
SYSTEM_CATALOG_ALIAS = "duckhaven"
SYSTEM_CATALOG_FEATURE = "system_catalog"
SYSTEM_CATALOG_ROW_FILTER_FEATURE = "system_catalog_row_filter"

# The workspace the query runs in, everyone it runs for (comma-separated ids), and
# the tables they may read (comma-separated ``schema.table``).
SETTING_WORKSPACE = "duckhaven.workspace"
SETTING_PRINCIPALS = "duckhaven.principals"
SETTING_READABLE = "duckhaven.readable"
ROW_FILTER_SETTINGS = (SETTING_WORKSPACE, SETTING_PRINCIPALS, SETTING_READABLE)

# What a setting's value may hold: lower-case ids and table names, and the
# separators between them. Nothing that libpq or a SQL string would treat as
# syntax (spaces, quotes, backslashes), so a value needs no escaping anywhere.
_SETTING_VALUE = re.compile(r"[0-9a-z_.,-]*")


def connection_options(settings: dict[str, str]) -> str:
    """The libpq ``options`` value that sets each of ``settings`` for the session.

    Raises ``ValueError`` for a name other than the row-filter settings, or a
    value outside the characters they use.
    """
    parts = []
    for name in ROW_FILTER_SETTINGS:
        value = settings.get(name, "")
        if not _SETTING_VALUE.fullmatch(value):
            raise ValueError(f"Invalid value for {name}")
        parts.append(f"-c {name}={value}")
    unknown = set(settings) - set(ROW_FILTER_SETTINGS)
    if unknown:
        raise ValueError(f"Unknown system catalog settings: {', '.join(sorted(unknown))}")
    return " ".join(parts)
