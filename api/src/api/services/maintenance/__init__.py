"""Autonomous lakehouse maintenance advisor.

V1 is recommend-only: it scans tables, computes an explainable health score, and
emits justified recommendations. It never performs maintenance — DuckHaven's
only Iceberg engine (DuckDB's ``iceberg`` extension) cannot yet expire snapshots,
compact files, or rewrite manifests. The recommendation model carries external
remediation guidance and is the seam for a future one-click apply.
"""
