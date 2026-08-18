"""Explainable lakehouse health scoring.

A table's health is a weighted average of four dimension sub-scores, each a
transparent linear function of one metric against the preset's warn/bad bounds.
Every sub-score is returned alongside its raw value and a plain-English sentence,
so the UI never shows a bare number. Higher-level scores (namespace, workspace,
deployment) are data-byte-weighted averages of table scores.

Pure functions only — no I/O, no DB. This is the most heavily unit-tested module.
"""

from __future__ import annotations

from typing import Any

# Dimension weights (sum to 100). Fragmentation dominates because small files are
# the most common and most query-impacting health problem.
WEIGHTS = {
    "fragmentation": 35,
    "snapshots": 25,
    "metadata": 20,
    "storage": 20,
}

# Score bands -> label/colour. Kept here so API and UI agree on thresholds.
HEALTHY_MIN = 90
FAIR_MIN = 70


def band(score: int | float | None) -> str:
    """Map a 0-100 score to a band label. ``None`` (no data) is ``unknown``."""
    if score is None:
        return "unknown"
    if score >= HEALTHY_MIN:
        return "healthy"
    if score >= FAIR_MIN:
        return "fair"
    return "attention"


def _linear_score(value: float, good: float, bad: float) -> float:
    """Map ``value`` to 0-100: 100 at the ``good`` bound, 0 at the ``bad`` bound,
    linear between, clamped outside. Works in either direction (good may be the
    lower or higher bound)."""
    if good == bad:
        return 100.0 if value == good else 0.0
    raw = 100.0 * (bad - value) / (bad - good)
    return max(0.0, min(100.0, raw))


def _human_bytes(n: float | None) -> str:
    if not n:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _factor(score: float, value: Any, detail: str, weight: int) -> dict[str, Any]:
    return {"score": round(score), "value": value, "detail": detail, "weight": weight}


def score_fragmentation(metrics: dict[str, Any], t: dict[str, float]) -> dict[str, Any] | None:
    ratio = metrics.get("small_file_ratio")
    if ratio is None:
        return None
    score = _linear_score(ratio, t["small_file_ratio_good"], t["small_file_ratio_bad"])
    target = _human_bytes(t["target_file_bytes"])
    detail = f"{ratio * 100:.0f}% of data files are below the {target} target size"
    return _factor(score, round(ratio, 4), detail, WEIGHTS["fragmentation"])


def score_snapshots(metrics: dict[str, Any], t: dict[str, float]) -> dict[str, Any] | None:
    age_days = metrics.get("oldest_snapshot_age_days")
    if age_days is not None:
        score = _linear_score(age_days, t["snapshot_retention_days"], t["snapshot_age_bad_days"])
        detail = (
            f"oldest snapshot is {age_days:.0f} days old "
            f"(target keeps ~{t['snapshot_retention_days']:.0f} days)"
        )
        return _factor(score, round(age_days, 4), detail, WEIGHTS["snapshots"])
    # Age is the honest signal (expiration is age-based); fall back to count when
    # the probe couldn't measure it.
    count = metrics.get("snapshot_count")
    if count is None:
        return None
    score = _linear_score(count, t["snapshot_min_keep"], t["snapshot_count_bad"])
    detail = f"{count} snapshots retained (target keeps ~{t['snapshot_retention_days']:.0f} days)"
    return _factor(score, count, detail, WEIGHTS["snapshots"])


def score_metadata(metrics: dict[str, Any], t: dict[str, float]) -> dict[str, Any] | None:
    """Manifest count relative to data files — the one cheap metadata-health signal.

    ``metadata_bytes`` is intentionally not measured: DuckDB's ``glob`` exposes no
    file sizes, so there is no cheap way to compute it, and the manifest ratio
    already captures metadata bloat.
    """
    data_files = metrics.get("data_file_count")
    manifests = metrics.get("manifest_count")
    if manifests is None or not data_files:
        return None
    ratio = manifests / data_files
    s = _linear_score(ratio, 0.0, t["manifest_per_datafile_bad"])
    detail = f"{manifests} manifests for {data_files} data files"
    return _factor(s, round(ratio, 4), detail, WEIGHTS["metadata"])


def score_storage(metrics: dict[str, Any], t: dict[str, float]) -> dict[str, Any] | None:
    orphan_bytes = metrics.get("orphan_bytes")
    total_bytes = metrics.get("total_data_bytes")
    if orphan_bytes is None or not total_bytes:
        return None
    ratio = orphan_bytes / total_bytes
    score = _linear_score(ratio, 0.0, t["orphan_ratio_bad"])
    detail = f"~{_human_bytes(orphan_bytes)} ({ratio * 100:.0f}%) of storage appears orphaned"
    return _factor(score, round(ratio, 4), detail, WEIGHTS["storage"])


_DIMENSIONS = {
    "fragmentation": score_fragmentation,
    "snapshots": score_snapshots,
    "metadata": score_metadata,
    "storage": score_storage,
}


def score_table(
    metrics: dict[str, Any], thresholds: dict[str, float]
) -> tuple[int | None, dict[str, Any]]:
    """Return ``(score, factors)`` for one table.

    Dimensions whose metric is missing are dropped and the remaining weights are
    renormalized, so a partial probe still yields a meaningful score over what it
    measured (rather than penalizing the table for missing data). When nothing is
    measurable the score is ``None`` (``band("unknown")``) — a fully-failed probe
    must not read as a perfect 100.
    """
    factors: dict[str, Any] = {}
    for name, fn in _DIMENSIONS.items():
        factor = fn(metrics, thresholds)
        if factor is not None:
            factors[name] = factor

    total_weight = sum(f["weight"] for f in factors.values())
    if not factors or total_weight == 0:
        return None, factors
    weighted = sum(f["score"] * f["weight"] for f in factors.values())
    return round(weighted / total_weight), factors


def aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll table scores up to a namespace/workspace/deployment summary.

    The headline score is data-byte-weighted (a 1 TB table outweighs a 1 MB one),
    but we also surface the table count and how many need attention so a few tiny
    unhealthy tables aren't hidden behind a healthy giant.

    ``samples`` is a list of ``{"score": int, "total_data_bytes": int|None}``.
    """
    scored = [s for s in samples if s.get("score") is not None]
    table_count = len(scored)
    if table_count == 0:
        return {
            "score": None,
            "band": "unknown",
            "table_count": 0,
            "attention_count": 0,
            "total_data_bytes": 0,
        }

    total_bytes = sum((s.get("total_data_bytes") or 0) for s in scored)
    if total_bytes > 0:
        weighted = sum(s["score"] * (s.get("total_data_bytes") or 0) for s in scored)
        score = round(weighted / total_bytes)
    else:
        # No size info anywhere -> fall back to an unweighted mean.
        score = round(sum(s["score"] for s in scored) / table_count)

    attention = sum(1 for s in scored if s["score"] < FAIR_MIN)
    return {
        "score": score,
        "band": band(score),
        "table_count": table_count,
        "attention_count": attention,
        "total_data_bytes": total_bytes,
    }
