import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { configure, bundle } = vi.hoisted(() => ({
  configure: vi.fn(),
  bundle: { fail: false },
}));

vi.mock("@/features/dashboards/editor/yamlBundle", () => ({
  get configureDashboardYaml() {
    if (bundle.fail) throw new Error("chunk failed to load");
    return configure;
  },
}));

// The loader keeps module-level state, so each test gets fresh modules.
async function importLoader() {
  return import("@/features/dashboards/editor/yamlLoader");
}

beforeEach(() => {
  vi.resetModules();
  configure.mockClear();
  bundle.fail = false;
});

const SCHEMA = { title: "DuckHaven dashboard" };

describe("the dashboard editor's YAML", () => {
  it("loads after Monaco and hands the schema over once", async () => {
    const { loadDashboardYaml } = await importLoader();

    await Promise.all([loadDashboardYaml(SCHEMA), loadDashboardYaml(SCHEMA)]);

    expect(configure).toHaveBeenCalledExactlyOnceWith(SCHEMA);
  });

  it("is ready once the schema has arrived and the YAML has loaded", async () => {
    const { useDashboardYamlReady } = await importLoader();

    const { result, rerender } = renderHook(
      ({ schema }: { schema?: object }) => useDashboardYamlReady(schema),
      { initialProps: {} },
    );
    expect(result.current).toBe(false);

    rerender({ schema: SCHEMA });
    await waitFor(() => expect(result.current).toBe(true));
  });

  it("lets a later editor try again after a failed load", async () => {
    bundle.fail = true;
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const { loadDashboardYaml, useDashboardYamlReady } = await importLoader();

    const { result } = renderHook(() => useDashboardYamlReady(SCHEMA));
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(result.current).toBe(false);

    bundle.fail = false;
    await loadDashboardYaml(SCHEMA);
    expect(configure).toHaveBeenCalledOnce();
    error.mockRestore();
  });
});
