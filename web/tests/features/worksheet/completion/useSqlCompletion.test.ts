import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { useSqlCompletion } from "@/features/worksheet/completion/useSqlCompletion";
import { updateCompletionContext } from "@/features/worksheet/completion/provider";
import { systemCatalog } from "@/mock/fixtures/systemCatalog";
import { createWrapper } from "@tests/utils";
import { server } from "@tests/mock/server";

vi.mock("@/features/worksheet/completion/provider", async (original) => ({
  ...(await original<typeof import("@/features/worksheet/completion/provider")>()),
  updateCompletionContext: vi.fn(),
}));

/** The catalogs the hook last offered for completion. */
function offered(): string[] {
  const calls = vi.mocked(updateCompletionContext).mock.calls;
  return calls.at(-1)?.[0].snapshot?.catalogs ?? [];
}

describe("useSqlCompletion and the system catalog", () => {
  beforeEach(() => {
    vi.mocked(updateCompletionContext).mockClear();
  });

  it("offers it with the tables the caller may read", async () => {
    const { wrapper } = createWrapper();
    renderHook(() => useSqlCompletion("acme-analytics", "acme_analytics"), {
      wrapper,
    });

    await waitFor(() => expect(offered()).toContain("duckhaven"));
  });

  it("does not offer it to someone who may read none of its tables", async () => {
    server.use(
      http.get("/api/workspaces/:ws/system-catalog", () =>
        HttpResponse.json({ ...systemCatalog, tables: [] }),
      ),
    );
    const { wrapper } = createWrapper();
    renderHook(() => useSqlCompletion("acme-analytics", "acme_analytics"), {
      wrapper,
    });

    await waitFor(() => expect(offered()).toContain("acme_analytics"));
    expect(offered()).not.toContain("duckhaven");
  });
});
