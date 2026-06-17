import { describe, it, expect } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import {
  useDeploymentHealth,
  useMaintenancePolicy,
  useRecommendations,
  useWorkspaceHealth,
} from "@/queries/maintenance";
import { createWrapper } from "@tests/utils";

describe("maintenance queries", () => {
  it("deployment health rolls up a data-weighted score and a workspace list", async () => {
    const { queryClient, wrapper } = createWrapper();
    const { result } = renderHook(() => useDeploymentHealth(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.summary.table_count).toBe(2);
    expect(result.current.data?.workspaces.length).toBe(1);
    queryClient.clear();
  });

  it("workspace health returns namespaces and tables sorted worst-first", async () => {
    const { queryClient, wrapper } = createWrapper();
    const { result } = renderHook(() => useWorkspaceHealth("demo"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const tables = result.current.data!.tables;
    expect(tables[0].score!).toBeLessThanOrEqual(tables[1].score!);
    queryClient.clear();
  });

  it("recommendations default to the open feed", async () => {
    const { queryClient, wrapper } = createWrapper();
    const { result } = renderHook(() => useRecommendations(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data!.length).toBeGreaterThan(0);
    expect(result.current.data!.every((r) => r.status === "open")).toBe(true);
    queryClient.clear();
  });

  it("policy exposes the resolved threshold bundle", async () => {
    const { queryClient, wrapper } = createWrapper();
    const { result } = renderHook(() => useMaintenancePolicy(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.preset).toBe("balanced");
    expect(result.current.data?.thresholds.target_file_bytes).toBeGreaterThan(0);
    queryClient.clear();
  });
});
