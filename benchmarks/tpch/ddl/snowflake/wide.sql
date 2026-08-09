-- Write scenario, wide shape (plan §4/config/scenarios.yaml): the
-- lineitem/orders fact flattened against every dimension it joins to
-- (customer+nation+region on both the customer and supplier side, part,
-- partsupp) into one denormalized table — the common "wide star-schema
-- flatten" CTAS pattern. Standard ANSI CTAS/joins, identical across all
-- three engines today; see narrow.sql's header for why this is
-- hand-written rather than generated.

CREATE TABLE tpch_write_wide AS
SELECT
    l.l_orderkey,
    l.l_linenumber,
    l.l_partkey,
    l.l_suppkey,
    l.l_quantity,
    l.l_extendedprice,
    l.l_discount,
    l.l_tax,
    l.l_returnflag,
    l.l_linestatus,
    l.l_shipdate,
    l.l_commitdate,
    l.l_receiptdate,
    o.o_orderstatus,
    o.o_totalprice,
    o.o_orderdate,
    o.o_orderpriority,
    o.o_clerk,
    o.o_shippriority,
    c.c_name AS customer_name,
    c.c_address AS customer_address,
    c.c_phone AS customer_phone,
    c.c_acctbal AS customer_acctbal,
    c.c_mktsegment AS customer_mktsegment,
    cn.n_name AS customer_nation,
    cr.r_name AS customer_region,
    s.s_name AS supplier_name,
    s.s_address AS supplier_address,
    s.s_phone AS supplier_phone,
    s.s_acctbal AS supplier_acctbal,
    sn.n_name AS supplier_nation,
    sr.r_name AS supplier_region,
    p.p_name AS part_name,
    p.p_mfgr AS part_mfgr,
    p.p_brand AS part_brand,
    p.p_type AS part_type,
    p.p_size AS part_size,
    p.p_container AS part_container,
    p.p_retailprice AS part_retailprice,
    ps.ps_supplycost,
    ps.ps_availqty
FROM lineitem l
JOIN orders o ON l.l_orderkey = o.o_orderkey
JOIN customer c ON o.o_custkey = c.c_custkey
JOIN nation cn ON c.c_nationkey = cn.n_nationkey
JOIN region cr ON cn.n_regionkey = cr.r_regionkey
JOIN supplier s ON l.l_suppkey = s.s_suppkey
JOIN nation sn ON s.s_nationkey = sn.n_nationkey
JOIN region sr ON sn.n_regionkey = sr.r_regionkey
JOIN part p ON l.l_partkey = p.p_partkey
JOIN partsupp ps ON l.l_partkey = ps.ps_partkey AND l.l_suppkey = ps.ps_suppkey;
