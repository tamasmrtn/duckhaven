import pytest

from api.services.sql_classify import STATEMENT_TYPES, classify_statement


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        # Reads. Everything that hands back a result grid reads as a select to
        # someone scanning History for "the queries I ran".
        ("SELECT 1", "select"),
        ("SELECT * FROM t WHERE x = 1", "select"),
        ("WITH c AS (SELECT 1) SELECT * FROM c", "select"),
        ("SELECT 1 UNION SELECT 2", "select"),
        ("(SELECT 1)", "select"),
        ("SUMMARIZE t", "select"),
        ("PRAGMA table_info('t')", "select"),
        # Writes.
        ("INSERT INTO t VALUES (1)", "insert"),
        ("UPDATE t SET x = 1", "update"),
        ("DELETE FROM t", "delete"),
        # DuckDB's TRUNCATE builds the same node family as DELETE and shares its
        # plan, so History groups the two rather than splitting a rare spelling.
        ("TRUNCATE TABLE t", "delete"),
        ("MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN UPDATE SET x = 1", "merge"),
        ("COPY t TO 's3://bucket/x.parquet'", "copy"),
        # DDL.
        ("CREATE TABLE t (a int)", "create"),
        ("CREATE TABLE t AS SELECT 1", "create"),
        ("CREATE OR REPLACE VIEW v AS SELECT 1", "create"),
        ("ALTER TABLE t ADD COLUMN b int", "alter"),
        ("DROP TABLE t", "drop"),
        # Introspection.
        ("DESCRIBE t", "describe"),
        ("SHOW TABLES", "describe"),
        # Parsed into a known node, but nothing more specific fits.
        ("USE cat.sch", "other"),
        ("BEGIN", "other"),
        ("COMMIT", "other"),
        ("ROLLBACK", "other"),
        ("SET memory_limit = '1GB'", "other"),
        ("ATTACH 'x' AS y", "other"),
        ("ANALYZE", "other"),
    ],
)
def test_classifies_every_statement_form_duckhaven_accepts(sql: str, expected: str):
    assert classify_statement(sql) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "SELECT FROM WHERE",  # a real parse error
        "$$$",
        # sqlglot does not raise on syntax it cannot model: it lexes the whole
        # statement as a raw Command. That is the parser saying "I do not know",
        # so it must classify as unknown rather than being swept into "other".
        "EXPLAIN SELECT 1",
        "CALL some_procedure()",
        "VACUUM",
    ],
)
def test_unparseable_and_unmodelled_statements_are_unknown_not_other(sql: str):
    """Null and "other" are different answers and must not be conflated.

    "other" claims the statement was understood; null admits it was not. The
    History filter relies on the distinction — a null row must never be swept up
    by a filter for a type it was never shown to be.
    """
    assert classify_statement(sql) is None


def test_multi_statement_script_is_classified_by_its_first_statement():
    assert classify_statement("SELECT 1; INSERT INTO t VALUES (2)") == "select"
    assert classify_statement("INSERT INTO t VALUES (2); SELECT 1") == "insert"


def test_taxonomy_is_the_eleven_values_the_api_validates_against():
    """The filter validates against this set, so it is part of the contract."""
    assert STATEMENT_TYPES == {
        "select",
        "insert",
        "update",
        "delete",
        "merge",
        "copy",
        "create",
        "alter",
        "drop",
        "describe",
        "other",
    }
