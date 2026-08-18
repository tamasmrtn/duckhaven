from api.services.maintenance import scoring
from api.services.maintenance.presets import resolve_thresholds

T = resolve_thresholds("balanced")
MIB = 1024 * 1024


def test_band_boundaries():
    assert scoring.band(100) == "healthy"
    assert scoring.band(90) == "healthy"
    assert scoring.band(89) == "fair"
    assert scoring.band(70) == "fair"
    assert scoring.band(69) == "attention"
    assert scoring.band(None) == "unknown"


def test_linear_score_direction_and_clamp():
    # good=0, bad=0.8: ratio 0 -> 100, ratio 0.8 -> 0, midpoint -> 50, clamps.
    assert scoring._linear_score(0.0, 0.0, 0.8) == 100.0
    assert scoring._linear_score(0.8, 0.0, 0.8) == 0.0
    assert scoring._linear_score(0.4, 0.0, 0.8) == 50.0
    assert scoring._linear_score(2.0, 0.0, 0.8) == 0.0
    assert scoring._linear_score(-1.0, 0.0, 0.8) == 100.0


def test_fragmentation_factor_explains_itself():
    # 0.45 is the midpoint between the good (0.10) and bad (0.80) bounds.
    f = scoring.score_fragmentation({"small_file_ratio": 0.45}, T)
    assert f is not None
    assert f["score"] == 50
    assert f["weight"] == 35
    assert "%" in f["detail"] and "target" in f["detail"]


def test_fragmentation_missing_metric_returns_none():
    assert scoring.score_fragmentation({}, T) is None


def test_snapshots_scores_on_age():
    # Age is the honest signal (expiration is age-based): at the retention target
    # it scores 100, at the bad bound it scores 0.
    f = scoring.score_snapshots({"oldest_snapshot_age_days": 7.0}, T)
    assert f is not None
    assert f["score"] == 100
    f = scoring.score_snapshots({"oldest_snapshot_age_days": 21.0}, T)
    assert f is not None
    assert f["score"] == 0


def test_snapshots_falls_back_to_count():
    # When age is missing, count is the fallback: 100 snapshots vs good 5 / bad
    # 500 is ~81.
    f = scoring.score_snapshots({"snapshot_count": 100}, T)
    assert f is not None
    assert f["score"] == 81
    assert scoring.score_snapshots({}, T) is None


def test_metadata_uses_manifest_ratio_only():
    # metadata_bytes is never populated (no cheap way to measure it), so the score
    # must ignore it and rely solely on the manifest-to-datafile ratio.
    metrics = {
        "manifest_count": 5,
        "data_file_count": 100,  # ratio 0.05 -> 90 against bad bound 0.50
        "metadata_bytes": 50 * MIB,
        "total_data_bytes": 100 * MIB,
    }
    f = scoring.score_metadata(metrics, T)
    assert f is not None
    assert f["score"] == 90
    assert "manifest" in f["detail"].lower()


def test_score_table_renormalizes_when_dimensions_missing():
    # Only fragmentation present (ratio 0.8 == bad bound -> 0). Score should be 0,
    # not diluted by absent dimensions defaulting to 100.
    score, factors = scoring.score_table({"small_file_ratio": 0.8}, T)
    assert score == 0
    assert set(factors) == {"fragmentation"}


def test_score_table_perfect_when_all_healthy():
    metrics = {
        "small_file_ratio": 0.0,
        "snapshot_count": 1,
        "manifest_count": 1,
        "data_file_count": 1000,
        "total_data_bytes": 100 * MIB,
        "orphan_bytes": 0,
    }
    score, factors = scoring.score_table(metrics, T)
    assert score == 100
    assert set(factors) == {"fragmentation", "snapshots", "metadata", "storage"}


def test_score_table_no_metrics_is_unknown():
    # Nothing measurable -> unknown, not a perfect 100 (a failed probe must not
    # read as "Healthy").
    score, factors = scoring.score_table({}, T)
    assert score is None
    assert factors == {}


def test_aggregate_is_data_byte_weighted():
    # A huge unhealthy table should drag the rollup far below a tiny healthy one.
    samples = [
        {"score": 100, "total_data_bytes": 1 * MIB},
        {"score": 0, "total_data_bytes": 999 * MIB},
    ]
    agg = scoring.aggregate(samples)
    assert agg["score"] < 5
    assert agg["table_count"] == 2
    assert agg["attention_count"] == 1
    assert agg["band"] == "attention"


def test_aggregate_falls_back_to_mean_without_sizes():
    agg = scoring.aggregate([{"score": 80, "total_data_bytes": None}, {"score": 60}])
    assert agg["score"] == 70


def test_aggregate_empty():
    agg = scoring.aggregate([])
    assert agg["score"] is None
    assert agg["table_count"] == 0
    assert agg["band"] == "unknown"
