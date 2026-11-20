"""Which tables a finished query read and wrote, as access.table_access records them."""

from __future__ import annotations

import uuid

import pytest

from api.services.system_catalog.table_access import accesses

CATALOGS = {"sales": (uuid.uuid4(), "sales"), "ops": (uuid.uuid4(), "Ops")}


def _found(sql: str, catalog: str | None = "sales", schema: str | None = "analytics"):
    return [
        (a.catalog_name, a.schema_name, a.table_name, a.access)
        for a in accesses(sql, catalog, schema, CATALOGS)
    ]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT * FROM orders AS o JOIN analytics.customers AS c ON o.id = c.id;",
            [("sales", "analytics", "customers", "read"), ("sales", "analytics", "orders", "read")],
        ),
        (
            "INSERT INTO sales.analytics.daily SELECT * FROM ops.raw.events;",
            [("Ops", "raw", "events", "read"), ("sales", "analytics", "daily", "write")],
        ),
        # `ops.events` names the catalog `ops`, so DuckDB binds it to its `main`.
        (
            "CREATE TABLE totals AS SELECT * FROM ops.events;",
            [("Ops", "main", "events", "read"), ("sales", "analytics", "totals", "write")],
        ),
        (
            "UPDATE Orders SET total = 0 FROM customers WHERE orders.id = customers.id;",
            [
                ("sales", "analytics", "customers", "read"),
                ("sales", "analytics", "orders", "write"),
            ],
        ),
        # A table is recorded once per kind of access, whatever the statement count.
        (
            "SELECT 1 FROM orders; SELECT 2 FROM ORDERS;",
            [("sales", "analytics", "orders", "read")],
        ),
        # A `USE` moves where the next statement's names resolve.
        ("USE ops.raw; SELECT * FROM events;", [("Ops", "raw", "events", "read")]),
        # Not table accesses.
        ("WITH t AS (SELECT 1) SELECT * FROM t;", []),
        ("DESCRIBE sales.analytics.orders;", []),
        ("SELECT * FROM duckhaven.access.audit;", []),
        ("SELECT * FROM information_schema.tables;", []),
        ("SELECT * FROM elsewhere.raw.events;", []),
        ("SELEC broken", []),
    ],
)
def test_what_a_query_read_and_wrote(sql, expected):
    assert _found(sql) == expected


def test_unqualified_names_are_left_out_when_where_they_resolve_is_unknown():
    assert _found("SELECT * FROM orders JOIN ops.raw.events USING (id);", None, None) == [
        ("Ops", "raw", "events", "read")
    ]


def test_a_catalog_without_a_schema_resolves_in_the_workspace_default_schema():
    assert _found("SELECT * FROM orders;", "sales", None) == [
        ("sales", "analytics", "orders", "read")
    ]
