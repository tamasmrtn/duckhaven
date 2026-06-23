"""The built-in, read-only **system catalog** (`duckhaven`).

A single DuckHaven-owned Polaris/Iceberg catalog, attached to every workspace by
default, exposing cross-workspace metadata and activity (query history, audit
events, an information-schema-equivalent). Modeled on Databricks' ``system``
catalog and Snowflake's ``SNOWFLAKE`` database.

Naming is forced by DuckDB reserved names (see :mod:`.constants`): the SQL
identifier is ``duckhaven`` (``system`` is reserved) and the metadata namespace
is ``info_schema`` (``information_schema`` is built in and collides).

Import from the submodules directly (``.constants`` / ``.bootstrap``); this
package keeps no re-exports so ``constants`` stays importable from low-level
code (slug validation) without pulling in ``bootstrap`` → ``workspace`` cycles.
"""
