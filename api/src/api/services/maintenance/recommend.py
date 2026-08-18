"""Recommendation rules.

Each rule turns a threshold crossing into a justified recommendation carrying the
fields the product requires: why (rationale), estimated impact, confidence, and
urgency (severity). V1 never applies these — ``remediation`` holds external
guidance (the standard Iceberg maintenance procedures) and ``applicable_in_app``
is always ``False`` until DuckDB's iceberg extension can perform the operation.

Pure functions only.
"""

from __future__ import annotations

from typing import Any

from api.services.maintenance.scoring import _human_bytes


def _severity(value: float, warn: float, bad: float) -> str | None:
    """Higher value is worse for every metric we score. Returns ``None`` below warn."""
    if value >= bad:
        return "critical"
    if value >= warn:
        return "warning"
    return None


def _rec(
    kind: str,
    severity: str,
    confidence: str,
    rationale: str,
    estimated_impact: dict[str, Any],
    remediation: dict[str, Any],
) -> dict[str, Any]:
    remediation = {"applicable_in_app": False, **remediation}
    return {
        "kind": kind,
        "severity": severity,
        "confidence": confidence,
        "rationale": rationale,
        "estimated_impact": estimated_impact,
        "remediation": remediation,
    }


def _compact(metrics: dict[str, Any], t: dict[str, float]) -> dict[str, Any] | None:
    ratio = metrics.get("small_file_ratio")
    if ratio is None:
        return None
    severity = _severity(ratio, t["small_file_ratio_warn"], t["small_file_ratio_bad"])
    if severity is None:
        return None
    data_files = metrics.get("data_file_count") or 0
    small_files = round(data_files * ratio)
    target = _human_bytes(t["target_file_bytes"])
    return _rec(
        "compact_small_files",
        severity,
        "high",
        f"{ratio * 100:.0f}% of {data_files} data files are below the {target} target. "
        "Compacting reduces file count and speeds up scans.",
        {"small_files": small_files, "data_files": data_files},
        {
            "summary": f"Compact data files to ~{target}.",
            "command": "CALL <catalog>.system.rewrite_data_files('<schema>.<table>')",
            "tool": "Spark / external Iceberg engine",
        },
    )


def _expire(metrics: dict[str, Any], t: dict[str, float]) -> dict[str, Any] | None:
    keep = int(t["snapshot_min_keep"])
    days = int(t["snapshot_retention_days"])
    age_days = metrics.get("oldest_snapshot_age_days")
    if age_days is not None:
        # Expiration is age-based, so score on the oldest snapshot's age rather
        # than the raw count (100 snapshots all created today don't need expiring).
        severity = _severity(age_days, t["snapshot_retention_days"], t["snapshot_age_bad_days"])
        if severity is None:
            return None
        return _rec(
            "expire_snapshots",
            severity,
            "high",
            f"the oldest snapshot is {age_days:.0f} days old. Expiring snapshots older "
            f"than {days} days trims metadata and lets old data files be cleaned up.",
            {"oldest_snapshot_age_days": round(age_days, 4), "retention_days": days},
            {
                "summary": f"Expire snapshots older than {days} days (keep at least {keep}).",
                "command": "CALL <catalog>.system.expire_snapshots('<schema>.<table>', "
                f"TIMESTAMP 'now - {days} days')",
                "tool": "Spark / external Iceberg engine",
            },
        )
    count = metrics.get("snapshot_count")
    if count is None:
        return None
    severity = _severity(count, t["snapshot_count_warn"], t["snapshot_count_bad"])
    if severity is None:
        return None
    removable = max(0, count - keep)
    return _rec(
        "expire_snapshots",
        severity,
        "high",
        f"{count} snapshots are retained. Expiring snapshots older than {days} days "
        "trims metadata and lets old data files be cleaned up.",
        {"snapshots": count, "removable_estimate": removable, "retention_days": days},
        {
            "summary": f"Expire snapshots older than {days} days (keep at least {keep}).",
            "command": "CALL <catalog>.system.expire_snapshots('<schema>.<table>', "
            f"TIMESTAMP 'now - {days} days')",
            "tool": "Spark / external Iceberg engine",
        },
    )


def _rewrite_manifests(metrics: dict[str, Any], t: dict[str, float]) -> dict[str, Any] | None:
    manifests = metrics.get("manifest_count")
    data_files = metrics.get("data_file_count")
    if manifests is None or not data_files:
        return None
    ratio = manifests / data_files
    severity = _severity(ratio, t["manifest_per_datafile_warn"], t["manifest_per_datafile_bad"])
    if severity is None:
        return None
    return _rec(
        "rewrite_manifests",
        severity,
        "high",
        f"{manifests} manifests track {data_files} data files. Rewriting manifests "
        "consolidates metadata and speeds up planning.",
        {"manifests": manifests, "data_files": data_files},
        {
            "summary": "Rewrite manifests to consolidate table metadata.",
            "command": "CALL <catalog>.system.rewrite_manifests('<schema>.<table>')",
            "tool": "Spark / external Iceberg engine",
        },
    )


def _cleanup_orphans(metrics: dict[str, Any], t: dict[str, float]) -> dict[str, Any] | None:
    orphan_bytes = metrics.get("orphan_bytes")
    total_bytes = metrics.get("total_data_bytes")
    if orphan_bytes is None or not total_bytes:
        return None
    ratio = orphan_bytes / total_bytes
    severity = _severity(ratio, t["orphan_ratio_warn"], t["orphan_ratio_bad"])
    if severity is None:
        return None
    # Low confidence on purpose: in-flight writes and shared files cause false
    # positives, so this is an estimate to investigate, never an instruction to delete.
    return _rec(
        "cleanup_orphans",
        severity,
        "low",
        f"~{_human_bytes(orphan_bytes)} of files under the table location are not "
        "referenced by the current metadata. Verify before removing — in-flight "
        "writes can look orphaned.",
        {"orphan_bytes": orphan_bytes, "orphan_file_count": metrics.get("orphan_file_count")},
        {
            "summary": "Investigate and remove confirmed orphan files.",
            "command": "CALL <catalog>.system.remove_orphan_files('<schema>.<table>')",
            "tool": "Spark / external Iceberg engine",
            "warning": "Estimate only — confirm files are unreferenced before deleting.",
        },
    )


def _investigate_growth(
    metrics: dict[str, Any], t: dict[str, float], history: list[dict[str, Any]] | None
) -> dict[str, Any] | None:
    """Flag abnormal storage growth using the earliest sample in the trend window."""
    current = metrics.get("total_data_bytes")
    if current is None or not history:
        return None
    baselines = [h["total_data_bytes"] for h in history if h.get("total_data_bytes")]
    if not baselines:
        return None
    baseline = min(baselines)
    if baseline <= 0:
        return None
    factor = current / baseline
    if factor < t["growth_factor_warn"]:
        return None
    severity = "warning" if factor < t["growth_factor_warn"] * 2 else "critical"
    return _rec(
        "investigate_growth",
        severity,
        "medium",
        f"Storage grew {factor:.1f}x (to {_human_bytes(current)}) over the trend window. "
        "Review the writers and partitioning for unexpected volume.",
        {"current_bytes": current, "baseline_bytes": baseline, "growth_factor": round(factor, 2)},
        {"summary": "Review write pipelines and partition strategy for this table."},
    )


def generate(
    metrics: dict[str, Any],
    thresholds: dict[str, float],
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """All recommendations a single table's latest sample warrants, worst first."""
    out = [
        _compact(metrics, thresholds),
        _expire(metrics, thresholds),
        _rewrite_manifests(metrics, thresholds),
        _cleanup_orphans(metrics, thresholds),
        _investigate_growth(metrics, thresholds, history),
    ]
    rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted((r for r in out if r is not None), key=lambda r: rank.get(r["severity"], 9))
