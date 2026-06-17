import type {
  MaintenancePolicy,
  Recommendation,
  TableHealth,
} from "@/types/maintenance";

const GIB = 1024 ** 3;

export const HEALTH_TABLES: TableHealth[] = [
  {
    schema_name: "analytics",
    table_name: "events",
    score: 58,
    band: "attention",
    scanned_at: new Date().toISOString(),
    snapshot_count: 142,
    data_file_count: 980,
    manifest_count: 64,
    total_data_bytes: 42 * GIB,
    avg_file_bytes: 9 * 1024 * 1024,
    small_file_ratio: 0.62,
    orphan_bytes: 3 * GIB,
    factors: {
      fragmentation: {
        score: 35,
        value: 0.62,
        detail: "62% of data files are below the 128.0 MB target size",
        weight: 35,
      },
      snapshots: {
        score: 72,
        value: 142,
        detail: "142 snapshots retained (target keeps ~7 days)",
        weight: 25,
      },
      metadata: {
        score: 60,
        value: 0.065,
        detail: "64 manifests for 980 data files",
        weight: 20,
      },
      storage: {
        score: 76,
        value: 0.071,
        detail: "~3.0 GB (7%) of storage appears orphaned",
        weight: 20,
      },
    },
  },
  {
    schema_name: "analytics",
    table_name: "users",
    score: 96,
    band: "healthy",
    scanned_at: new Date().toISOString(),
    snapshot_count: 8,
    data_file_count: 40,
    manifest_count: 3,
    total_data_bytes: 8 * GIB,
    avg_file_bytes: 200 * 1024 * 1024,
    small_file_ratio: 0.05,
    orphan_bytes: 0,
    factors: {
      fragmentation: {
        score: 94,
        value: 0.05,
        detail: "5% of data files are below the 128.0 MB target size",
        weight: 35,
      },
    },
  },
];

export const RECOMMENDATIONS: Recommendation[] = [
  {
    id: "rec-1",
    workspace_id: "ws-1",
    schema_name: "analytics",
    table_name: "events",
    kind: "compact_small_files",
    severity: "critical",
    confidence: "high",
    rationale:
      "62% of 980 data files are below the 128.0 MB target. Compacting reduces file count and speeds up scans.",
    estimated_impact: { small_files: 608, data_files: 980 },
    remediation: {
      applicable_in_app: false,
      summary: "Compact data files to ~128.0 MB.",
      command: "CALL <catalog>.system.rewrite_data_files('analytics.events')",
      tool: "Spark / external Iceberg engine",
    },
    status: "open",
    created_at: new Date().toISOString(),
    resolved_at: null,
  },
  {
    id: "rec-2",
    workspace_id: "ws-1",
    schema_name: "analytics",
    table_name: "events",
    kind: "expire_snapshots",
    severity: "warning",
    confidence: "high",
    rationale:
      "142 snapshots are retained. Expiring snapshots older than 7 days trims metadata.",
    estimated_impact: {
      snapshots: 142,
      removable_estimate: 141,
      retention_days: 7,
    },
    remediation: {
      applicable_in_app: false,
      summary: "Expire snapshots older than 7 days (keep at least 1).",
      command:
        "CALL <catalog>.system.expire_snapshots('analytics.events', TIMESTAMP 'now - 7 days')",
      tool: "Spark / external Iceberg engine",
    },
    status: "open",
    created_at: new Date().toISOString(),
    resolved_at: null,
  },
];

export const POLICY: MaintenancePolicy = {
  scan_enabled: true,
  scan_frequency: "daily",
  preset: "balanced",
  thresholds: {
    target_file_bytes: 134217728,
    small_file_ratio_warn: 0.3,
    small_file_ratio_bad: 0.8,
    snapshot_retention_days: 7,
    snapshot_min_keep: 1,
    snapshot_count_warn: 100,
    snapshot_count_bad: 500,
    manifest_per_datafile_warn: 0.1,
    manifest_per_datafile_bad: 0.5,
    metadata_ratio_warn: 0.05,
    metadata_ratio_bad: 0.2,
    orphan_ratio_warn: 0.05,
    orphan_ratio_bad: 0.3,
    growth_factor_warn: 2,
  },
  max_tables_per_cycle: 50,
  last_scan_at: new Date().toISOString(),
  last_deep_scan_at: new Date().toISOString(),
};

export function resetMaintenance(): void {
  for (const r of RECOMMENDATIONS) r.status = "open";
}
