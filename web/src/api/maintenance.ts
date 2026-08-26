import { get, post, put, getAllPages } from "./client";
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
  tableHealth: (ws: string, catalog: string, schema: string, table: string) =>
    get<TableHealthDetail>(
      `/workspaces/${ws}/catalogs/${catalog}/schemas/${schema}/tables/${table}/health`,
    ),
  // `status` is no longer defaulted server-side, so the caller says which
  // states it wants; omitting it returns every state.
  recommendations: (status = "open") =>
    getAllPages<Recommendation>("/maintenance/recommendations", { status }),
  dismiss: (id: string) =>
    post<Recommendation>(`/maintenance/recommendations/${id}/dismiss`),

  // Admin.
  policy: () => get<MaintenancePolicy>("/admin/maintenance/policy"),
  updatePolicy: (body: PolicyUpdate) =>
    put<MaintenancePolicy>("/admin/maintenance/policy", body),
  scan: () => post<ScanResult>("/admin/maintenance/scan"),
};
