"""Stable names for the system catalog. No imports from other api modules so
this can be referenced from low-level code (slug validation) without cycles."""

from __future__ import annotations

# DuckDB reserves ``system`` as a catalog name (``ATTACH ... AS system`` is a
# BinderException), so the system catalog is addressed as ``duckhaven`` in SQL:
# ``SELECT * FROM duckhaven.query.history``. The UI/docs still call it the
# "system catalog"; only the SQL identifier differs.
SYSTEM_CATALOG_SLUG = "duckhaven"
# Display name shown in the catalog browser.
SYSTEM_CATALOG_NAME = "System"

# Namespaces (Polaris namespaces == DuckDB schemas) exposed by the catalog.
# ``info_schema`` rather than ``information_schema``: DuckDB injects a built-in
# ``information_schema`` into every attached catalog, so a same-named Iceberg
# namespace collides ("schema already exists"). ``info_schema`` is the
# information-schema-equivalent for cross-workspace object metadata.
QUERY_NAMESPACE = "query"
ACCESS_NAMESPACE = "access"
INFO_NAMESPACE = "info_schema"

SYSTEM_NAMESPACES = (QUERY_NAMESPACE, ACCESS_NAMESPACE, INFO_NAMESPACE)

# Slugs a user may not create (reserved for built-ins).
RESERVED_CATALOG_SLUGS = frozenset({SYSTEM_CATALOG_SLUG})
