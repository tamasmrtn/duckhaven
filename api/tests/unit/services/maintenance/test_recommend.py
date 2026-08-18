from api.services.maintenance import recommend
from api.services.maintenance.presets import resolve_thresholds

T = resolve_thresholds("balanced")
MIB = 1024 * 1024


def _kinds(recs):
    return {r["kind"] for r in recs}


def test_no_recommendations_for_healthy_table():
    metrics = {
        "small_file_ratio": 0.05,
        "snapshot_count": 3,
        "manifest_count": 2,
        "data_file_count": 1000,
        "orphan_bytes": 0,
        "total_data_bytes": 100 * MIB,
    }
    assert recommend.generate(metrics, T) == []


def test_compaction_fires_above_warn_with_high_confidence():
    recs = recommend.generate({"small_file_ratio": 0.4, "data_file_count": 200}, T)
    compact = next(r for r in recs if r["kind"] == "compact_small_files")
    assert compact["severity"] == "warning"
    assert compact["confidence"] == "high"
    assert compact["estimated_impact"]["small_files"] == 80
    assert compact["remediation"]["applicable_in_app"] is False


def test_compaction_critical_past_bad_bound():
    recs = recommend.generate({"small_file_ratio": 0.85, "data_file_count": 200}, T)
    compact = next(r for r in recs if r["kind"] == "compact_small_files")
    assert compact["severity"] == "critical"


def test_expire_snapshots_estimates_removable():
    recs = recommend.generate({"snapshot_count": 150}, T)
    expire = next(r for r in recs if r["kind"] == "expire_snapshots")
    assert expire["severity"] == "warning"
    assert expire["estimated_impact"]["removable_estimate"] == 145


def test_expire_fires_on_age():
    # Age is the honest signal: at the retention target it warns, at the bad bound
    # it is critical; below retention it stays quiet.
    recs = recommend.generate({"oldest_snapshot_age_days": 7.0}, T)
    expire = next(r for r in recs if r["kind"] == "expire_snapshots")
    assert expire["severity"] == "warning"
    assert expire["estimated_impact"]["oldest_snapshot_age_days"] == 7.0

    recs = recommend.generate({"oldest_snapshot_age_days": 21.0}, T)
    expire = next(r for r in recs if r["kind"] == "expire_snapshots")
    assert expire["severity"] == "critical"

    assert recommend.generate({"oldest_snapshot_age_days": 3.0}, T) == []


def test_orphan_recommendation_is_low_confidence():
    recs = recommend.generate(
        {"orphan_bytes": 20 * MIB, "total_data_bytes": 100 * MIB, "orphan_file_count": 12}, T
    )
    orphan = next(r for r in recs if r["kind"] == "cleanup_orphans")
    assert orphan["confidence"] == "low"
    assert "warning" in orphan["remediation"]


def test_growth_requires_history():
    metrics = {"total_data_bytes": 100 * MIB}
    assert recommend.generate(metrics, T) == []
    history = [{"total_data_bytes": 10 * MIB}]
    recs = recommend.generate(metrics, T, history=history)
    growth = next(r for r in recs if r["kind"] == "investigate_growth")
    assert growth["estimated_impact"]["growth_factor"] == 10.0
    assert growth["severity"] == "critical"  # 10x > 2 * warn(2.0)


def test_recommendations_sorted_critical_first():
    metrics = {
        "small_file_ratio": 0.85,  # critical
        "data_file_count": 100,
        "snapshot_count": 120,  # warning (warn 100, bad 500)
    }
    recs = recommend.generate(metrics, T)
    assert [r["severity"] for r in recs] == sorted(
        [r["severity"] for r in recs], key=lambda s: {"critical": 0, "warning": 1}[s]
    )
    assert recs[0]["severity"] == "critical"


def test_rewrite_manifests_needs_data_files():
    assert recommend.generate({"manifest_count": 50}, T) == []
    recs = recommend.generate({"manifest_count": 50, "data_file_count": 100}, T)
    assert "rewrite_manifests" in _kinds(recs)
