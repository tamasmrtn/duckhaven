import type { HealthBand, Severity } from "@/types/maintenance";

// Reuse the existing status colour tokens so health reads consistently with the
// rest of the app (success/running/failed/queued).
export const BAND_COLOR: Record<HealthBand, string> = {
  healthy: "var(--status-success)",
  fair: "var(--status-running)",
  attention: "var(--status-failed)",
  unknown: "var(--status-queued)",
};

export const BAND_LABEL: Record<HealthBand, string> = {
  healthy: "Healthy",
  fair: "Fair",
  attention: "Needs attention",
  unknown: "No data",
};

export const SEVERITY_COLOR: Record<Severity, string> = {
  critical: "var(--status-failed)",
  warning: "var(--status-running)",
  info: "var(--status-queued)",
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  warning: "Warning",
  info: "Info",
};

// Human-friendly titles for each recommendation kind.
export const KIND_LABEL: Record<string, string> = {
  compact_small_files: "Compact small files",
  expire_snapshots: "Expire old snapshots",
  rewrite_manifests: "Rewrite manifests",
  cleanup_orphans: "Clean up orphan files",
  investigate_growth: "Investigate storage growth",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}
