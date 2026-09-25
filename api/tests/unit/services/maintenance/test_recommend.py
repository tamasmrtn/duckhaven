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


# --- Per-kind remediation ---------------------------------------------------
# The findings are format-neutral; the fix is not.


def _small_files_metrics() -> dict:
    return {"small_file_ratio": 0.9, "data_file_count": 400}


def test_iceberg_remediation_is_unchanged():
    recs = recommend.generate(_small_files_metrics(), T)
    compact = next(r for r in recs if r["kind"] == "compact_small_files")
    assert "rewrite_data_files" in compact["remediation"]["command"]
    assert compact["remediation"]["tool"] == "Spark / external Iceberg engine"
    assert compact["remediation"]["applicable_in_app"] is False


def test_ducklake_remediation_names_commands_duckdb_can_run():
    recs = recommend.generate(_small_files_metrics(), T, catalog_kind="ducklake")
    compact = next(r for r in recs if r["kind"] == "compact_small_files")
    assert "ducklake_merge_adjacent_files" in compact["remediation"]["command"]
    assert compact["remediation"]["tool"] == "DuckDB (ducklake extension)"


def test_ducklake_snapshot_expiry_is_catalog_level():
    """`expire_older_than` has global scope, so the command cannot name a table."""
    metrics = {"snapshot_count": 500, "oldest_snapshot_age_days": 400}
    recs = recommend.generate(metrics, T, catalog_kind="ducklake")
    expire = next((r for r in recs if r["kind"] == "expire_snapshots"), None)
    assert expire is not None
    command = expire["remediation"]["command"]
    assert "ducklake_expire_snapshots" in command
    assert "<schema>" not in command and "<table>" not in command


def test_manifest_rewrites_are_dropped_for_ducklake():
    """No DuckLake counterpart, so it is dropped rather than given a fake command."""
    metrics = {"manifest_count": 5000, "data_file_count": 10}
    iceberg = recommend.generate(metrics, T)
    ducklake = recommend.generate(metrics, T, catalog_kind="ducklake")
    assert any(r["kind"] == "rewrite_manifests" for r in iceberg)
    assert not any(r["kind"] == "rewrite_manifests" for r in ducklake)


def test_ducklake_still_advises_rather_than_applying():
    """DuckDB can run these, but DuckHaven does not yet; this flag flips when it does."""
    recs = recommend.generate(_small_files_metrics(), T, catalog_kind="ducklake")
    assert all(r["remediation"]["applicable_in_app"] is False for r in recs)


# --- Applicability ---------------------------------------------------------
# Decided by the catalog kind's capability, not the finding.

FRAGMENTED = {"small_file_ratio": 0.4, "data_file_count": 200}


def _ducklake(metrics, *, can_apply):
    return recommend.generate(metrics, T, catalog_kind="ducklake", can_apply=can_apply)


def test_a_ducklake_recommendation_is_applicable_when_the_kind_says_so():
    compact = next(
        r for r in _ducklake(FRAGMENTED, can_apply=True) if r["kind"] == "compact_small_files"
    )

    assert compact["remediation"]["applicable_in_app"] is True
    assert compact["remediation"]["scope"] == "table"
    # The command shown is the one Apply runs, from the same renderer, so the
    # Copy button and the button beside it cannot disagree.
    assert compact["remediation"]["command"].startswith("CALL ducklake_merge_adjacent_files(")


def test_a_ducklake_recommendation_is_not_applicable_when_the_kind_says_not():
    """The capability is the switch, so turning it off leaves the advice and
    removes only the button."""
    compact = next(
        r for r in _ducklake(FRAGMENTED, can_apply=False) if r["kind"] == "compact_small_files"
    )

    assert compact["remediation"]["applicable_in_app"] is False
    assert compact["remediation"]["command"].startswith("CALL ducklake_")


def test_a_catalog_scoped_recommendation_says_so():
    """Expiring snapshots from one table's page expires every table's, and the
    confirmation cannot say that unless the finding carries it."""
    metrics = {**FRAGMENTED, "snapshot_count": 500, "oldest_snapshot_age_days": 400}
    expire = next(r for r in _ducklake(metrics, can_apply=True) if r["kind"] == "expire_snapshots")

    assert expire["remediation"]["scope"] == "catalog"


def test_the_displayed_cleanup_command_is_bounded_by_retention():
    """It was `cleanup_all => true`, which deletes files older snapshots still
    reference -- so anyone who copied it lost their time travel."""
    metrics = {**FRAGMENTED, "orphan_bytes": 50 * MIB, "total_data_bytes": 100 * MIB}
    recs = _ducklake(metrics, can_apply=True)
    for rec in recs:
        assert "cleanup_all" not in rec["remediation"].get("command", "")


def test_growth_prescribes_nothing_so_is_never_applicable():
    metrics = {**FRAGMENTED, "total_data_bytes": 10_000 * MIB}
    history = [{"total_data_bytes": 1 * MIB}]
    recs = recommend.generate(metrics, T, history=history, catalog_kind="ducklake", can_apply=True)
    for rec in recs:
        if rec["kind"] == "investigate_growth":
            assert rec["remediation"]["applicable_in_app"] is False
