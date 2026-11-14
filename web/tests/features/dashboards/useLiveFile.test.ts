import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { server } from "@tests/mock/server";
import { useLiveFile } from "@/features/dashboards/editor/useLiveFile";
import { PREVIEWS } from "@/mock/fixtures/dashboards";

const FILE = `version: 1
name: plan
title: Plan
rows:
  - widgets:
      - id: about
        type: text
        markdown: Hello.
`;

const settled = { timeout: 3000 };

describe("useLiveFile", () => {
  it("holds the file in a preview, and starts another once that one expires", async () => {
    const { result, rerender } = renderHook(
      ({ text }) => useLiveFile("acme-analytics", text),
      { initialProps: { text: FILE } },
    );
    await waitFor(() => expect(result.current.current).toBe(true), settled);
    const first = result.current.preview!.id;

    // Two hours unused, and the server has let it go.
    delete PREVIEWS[first];
    rerender({ text: FILE.replace("Plan", "Plan B") });

    await waitFor(() => expect(result.current.current).toBe(true), settled);
    expect(result.current.preview!.id).not.toBe(first);
    expect(result.current.preview!.spec.title).toBe("Plan B");
    expect(result.current.failure).toBeNull();
  });

  it("says why the preview could not be updated when the file is not to blame", async () => {
    server.use(
      http.post("/api/workspaces/:ws/dashboard-previews", () =>
        HttpResponse.json(
          { error: "internal_error", message: "The server failed.", details: null },
          { status: 500 },
        ),
      ),
    );
    const { result } = renderHook(() => useLiveFile("acme-analytics", FILE));

    await waitFor(
      () => expect(result.current.failure).toBe("The server failed."),
      settled,
    );
    expect(result.current.preview).toBeNull();
    expect(result.current.refused).toBe(false);
  });
});
