import { get, post, put } from "./client";
import type {
  DeploymentHealth,
  MaintenancePolicy,
  PolicyUpdate,
  Recommendation,
  ScanResult,
  TableHealthDetail,
  WorkspaceHealthDetail,
} from "@/types/maintenance";

export const maintenanceApi = {
  deploymentHealth: () => get<DeploymentHealth>("/maintenance/health"),
  workspaceHealth: (ws: string) =>
    get<WorkspaceHealthDetail>(`/workspaces/${ws}/health`),
  tableHealth: (ws: string, schema: string, table: string) =>
    get<TableHealthDetail>(
      `/workspaces/${ws}/schemas/${schema}/tables/${table}/health`,
    ),
  recommendations: (status = "open") =>
    get<Recommendation[]>(
      `/maintenance/recommendations?status=${encodeURIComponent(status)}`,
    ),
  dismiss: (id: string) =>
    post<Recommendation>(`/maintenance/recommendations/${id}/dismiss`),

  // Admin.
  policy: () => get<MaintenancePolicy>("/admin/maintenance/policy"),
  updatePolicy: (body: PolicyUpdate) =>
    put<MaintenancePolicy>("/admin/maintenance/policy", body),
  scan: () => post<ScanResult>("/admin/maintenance/scan"),
};
