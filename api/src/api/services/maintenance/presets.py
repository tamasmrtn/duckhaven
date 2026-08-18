"""Maintenance presets: the few knobs we expose, expanded into a threshold bundle.

A preset *is* the score definition and the recommendation sensitivity — choosing
"balanced" resolves every threshold below. The admin UI's Advanced section may
override individual values, but the preset is what almost everyone touches.

Thresholds come in pairs: a ``*_warn`` value (where a recommendation fires / the
amber band begins) and a ``*_bad`` value (where the dimension sub-score hits 0).
Keeping both explicit is what makes the score explainable rather than magic.
"""

from __future__ import annotations

# Target data-file size per preset. 256 MiB is the common Iceberg default;
# 128 MiB favours low-latency scans, 512 MiB favours batch throughput.
_TARGET_FILE_BYTES = {
    "conservative": 512 * 1024 * 1024,
    "balanced": 256 * 1024 * 1024,
    "aggressive": 128 * 1024 * 1024,
}

# Each preset only shifts the *sensitivity* — the structure is identical so the
# UI's Advanced override can edit any key without special-casing presets.
PRESETS: dict[str, dict[str, float]] = {
    "conservative": {
        "target_file_bytes": _TARGET_FILE_BYTES["conservative"],
        "small_file_ratio_good": 0.20,
        "small_file_ratio_warn": 0.50,
        "small_file_ratio_bad": 0.90,
        "snapshot_retention_days": 30,
        "snapshot_age_bad_days": 90,
        "snapshot_min_keep": 10,
        "snapshot_count_warn": 250,
        "snapshot_count_bad": 1000,
        "manifest_per_datafile_warn": 0.20,
        "manifest_per_datafile_bad": 0.75,
        "metadata_ratio_warn": 0.10,
        "metadata_ratio_bad": 0.40,
        "orphan_ratio_warn": 0.10,
        "orphan_ratio_bad": 0.50,
        "growth_factor_warn": 4.0,
    },
    "balanced": {
        "target_file_bytes": _TARGET_FILE_BYTES["balanced"],
        "small_file_ratio_good": 0.10,
        "small_file_ratio_warn": 0.30,
        "small_file_ratio_bad": 0.80,
        "snapshot_retention_days": 7,
        "snapshot_age_bad_days": 21,
        "snapshot_min_keep": 5,
        "snapshot_count_warn": 100,
        "snapshot_count_bad": 500,
        "manifest_per_datafile_warn": 0.10,
        "manifest_per_datafile_bad": 0.50,
        "metadata_ratio_warn": 0.05,
        "metadata_ratio_bad": 0.20,
        "orphan_ratio_warn": 0.05,
        "orphan_ratio_bad": 0.30,
        "growth_factor_warn": 2.0,
    },
    "aggressive": {
        "target_file_bytes": _TARGET_FILE_BYTES["aggressive"],
        "small_file_ratio_good": 0.05,
        "small_file_ratio_warn": 0.15,
        "small_file_ratio_bad": 0.60,
        "snapshot_retention_days": 3,
        "snapshot_age_bad_days": 9,
        "snapshot_min_keep": 2,
        "snapshot_count_warn": 50,
        "snapshot_count_bad": 250,
        "manifest_per_datafile_warn": 0.05,
        "manifest_per_datafile_bad": 0.30,
        "metadata_ratio_warn": 0.03,
        "metadata_ratio_bad": 0.15,
        "orphan_ratio_warn": 0.03,
        "orphan_ratio_bad": 0.20,
        "growth_factor_warn": 1.5,
    },
}

# Cadence each preset implies when the admin changes preset without an explicit
# frequency. Aggressive scans hourly; the others stay daily.
PRESET_FREQUENCIES = {"conservative": "daily", "balanced": "daily", "aggressive": "hourly"}

DEFAULT_PRESET = "balanced"
PRESET_NAMES = tuple(PRESETS)


def resolve_thresholds(preset: str, overrides: dict[str, float] | None = None) -> dict[str, float]:
    """Expand a preset name into a full threshold bundle, applying any overrides.

    Unknown presets fall back to the default; unknown override keys are ignored
    so a stale UI can't inject arbitrary fields.
    """
    base = dict(PRESETS.get(preset, PRESETS[DEFAULT_PRESET]))
    if overrides:
        base.update({k: v for k, v in overrides.items() if k in base})
    return base
