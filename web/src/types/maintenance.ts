export type HealthBand = "healthy" | "fair" | "attention" | "unknown";
export type Severity = "info" | "warning" | "critical";
export type Confidence = "low" | "medium" | "high";
export type ScanFrequency = "off" | "hourly" | "daily";
export type Preset = "conservative" | "balanced" | "aggressive";

export interface HealthFactor {
  score: number;
  value?: number | string | null;
  detail: string;
  weight: number;
}

export interface HealthSummary {
  score: number | null;
  band: HealthBand;
  table_count: number;
  attention_count: number;
  total_data_bytes: number;
}

export interface TableHealth {
  schema_name: string;
  table_name: string;
  score: number | null;
  band: HealthBand;
  scanned_at: string | null;
  snapshot_count: number | null;
  data_file_count: number | null;
  manifest_count: number | null;
  total_data_bytes: number | null;
  avg_file_bytes: number | null;
  small_file_ratio: number | null;
  orphan_bytes: number | null;
  factors: Record<string, HealthFactor> | null;
}

export interface WorkspaceHealth {
  workspace_id: string;
  slug: string;
  summary: HealthSummary;
}

export interface DeploymentHealth {
  summary: HealthSummary;
  workspaces: WorkspaceHealth[];
}

export interface NamespaceHealth {
  schema_name: string;
  summary: HealthSummary;
}

export interface WorkspaceHealthDetail {
  summary: HealthSummary;
  namespaces: NamespaceHealth[];
  tables: TableHealth[];
}

export interface HealthHistoryPoint {
  scanned_at: string;
  score: number | null;
  total_data_bytes: number | null;
}

export interface Remediation {
  applicable_in_app: boolean;
  summary?: string;
  command?: string;
  tool?: string;
  warning?: string;
}

export interface Recommendation {
  id: string;
  workspace_id: string;
  schema_name: string;
  table_name: string;
  kind: string;
  severity: Severity;
  confidence: Confidence;
  rationale: string;
  estimated_impact: Record<string, number | string | null> | null;
  remediation: Remediation | null;
  status: string;
  created_at: string;
  resolved_at: string | null;
}

export interface TableHealthDetail {
  table: TableHealth;
  history: HealthHistoryPoint[];
  recommendations: Recommendation[];
}

export interface MaintenancePolicy {
  scan_enabled: boolean;
  scan_frequency: ScanFrequency;
  preset: Preset;
  thresholds: Record<string, number>;
  max_tables_per_cycle: number;
  last_scan_at: string | null;
  last_deep_scan_at: string | null;
}

export interface PolicyUpdate {
  scan_enabled?: boolean;
  scan_frequency?: ScanFrequency;
  preset?: Preset;
  thresholds?: Record<string, number>;
  max_tables_per_cycle?: number;
}

export interface ScanResult {
  status: string;
  dispatched: number;
  candidates: number;
  stale: number;
  deep: boolean;
}
