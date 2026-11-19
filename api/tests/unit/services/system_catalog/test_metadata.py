"""What a finished statement marks for info_schema to re-read."""

from __future__ import annotations

import uuid

import pytest

from api.services.system_catalog.metadata import Change, _Attachments, changes, resolve


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("CREATE TABLE sales.analytics.orders (id INT);", [Change("sales", "analytics", "orders")]),
        ("CREATE OR REPLACE TABLE orders AS SELECT 1;", [Change(None, None, "orders")]),
        ("DROP TABLE IF EXISTS analytics.orders;", [Change(None, "analytics", "orders")]),
        ("INSERT INTO analytics.orders SELECT 1;", [Change(None, "analytics", "orders")]),
        ("UPDATE orders SET id = 2;", [Change(None, None, "orders")]),
        ("DELETE FROM orders;", [Change(None, None, "orders")]),
        ("TRUNCATE orders;", [Change(None, None, "orders")]),
        ("COPY orders FROM 's3://bucket/orders.parquet';", [Change(None, None, "orders")]),
        (
            "ALTER TABLE sales.analytics.orders RENAME TO orders_old;",
            [Change("sales", "analytics", "orders"), Change("sales", "analytics", "orders_old")],
        ),
        ("ALTER TABLE orders ADD COLUMN note VARCHAR;", [Change(None, None, "orders")]),
        # A schema re-reads its whole catalog.
        ("CREATE SCHEMA sales.analytics;", [Change("sales", None, None)]),
        ("DROP SCHEMA analytics CASCADE;", [Change(None, None, None)]),
        # Not listed in info_schema.
        ("CREATE VIEW v AS SELECT 1;", []),
        ("CREATE MACRO twice(x) AS x * 2;", []),
        ("COPY orders TO 'out.parquet';", []),
        # Unreadable: the current catalog, for all this can tell.
        ("CREATE TABLE (;", [Change(None, None, None)]),
    ],
)
def test_what_a_statement_changes(sql, expected):
    assert changes(sql) == expected


SALES, OPS = uuid.uuid4(), uuid.uuid4()
ATTACHED = _Attachments(by_name={"sales": SALES, "ops": OPS}, default=SALES)


@pytest.mark.parametrize(
    ("change", "active", "expected"),
    [
        (Change("sales", "analytics", "orders"), (None, None), [(SALES, "analytics", "orders")]),
        (Change("SALES", "analytics", "orders"), (None, None), [(SALES, "analytics", "orders")]),
        # Unqualified: the current catalog and schema.
        (Change(None, None, "orders"), ("ops", "raw"), [(OPS, "raw", "orders")]),
        (Change(None, None, "orders"), (None, "raw"), [(SALES, "raw", "orders")]),
        # No schema to go on: the whole catalog.
        (Change(None, None, "orders"), ("ops", None), [(OPS, None, None)]),
        (Change(None, None, None), ("ops", None), [(OPS, None, None)]),
        # `ops.orders` is a schema of the current catalog, or the catalog `ops`.
        (
            Change(None, "ops", "orders"),
            (None, None),
            [(OPS, None, None), (SALES, "ops", "orders")],
        ),
        # A catalog the workspace does not have.
        (Change("elsewhere", "analytics", "orders"), (None, None), []),
        (Change("ops", None, None), (None, None), [(OPS, None, None)]),
    ],
)
def test_which_catalog_a_change_marks(change, active, expected):
    assert resolve(change, ATTACHED, *active) == expected
