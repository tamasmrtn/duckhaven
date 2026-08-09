-- Write scenario, narrow shape (plan §4/config/scenarios.yaml): a CTAS of
-- a single table's own columns, no joins. Standard ANSI CTAS, identical
-- across all three engines today — see queries/dialect/DIFFS.md's sibling
-- reasoning for the 22 read queries; this file is hand-written rather than
-- generated because the ddl/ tree has no canonical/dialect split, but the
-- same rule applies: copy unmodified across engines until a real
-- incompatibility is observed running it.

CREATE TABLE tpch_write_narrow AS
SELECT
    l_orderkey,
    l_linenumber,
    l_partkey,
    l_suppkey,
    l_quantity,
    l_extendedprice,
    l_discount,
    l_tax,
    l_returnflag,
    l_linestatus,
    l_shipdate
FROM lineitem;
