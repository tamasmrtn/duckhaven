import { describe, it, expect, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { BASE_TITLE, useDocumentTitle } from "@/hooks/useDocumentTitle";

describe("useDocumentTitle", () => {
  beforeEach(() => {
    document.title = BASE_TITLE;
  });

  it("prefixes the page name onto the product name", () => {
    renderHook(() => useDocumentTitle("Catalog"));
    expect(document.title).toBe("Catalog · DuckHaven");
  });

  it("returns to the product name on unmount", () => {
    const { unmount } = renderHook(() => useDocumentTitle("History"));
    unmount();
    expect(document.title).toBe(BASE_TITLE);
  });

  it("follows the title as it changes", () => {
    const { rerender } = renderHook(({ t }) => useDocumentTitle(t), {
      initialProps: { t: "customer" },
    });
    rerender({ t: "orders" });
    expect(document.title).toBe("orders · DuckHaven");
  });

  it("leaves the title alone for null or empty", () => {
    renderHook(() => useDocumentTitle(null));
    expect(document.title).toBe(BASE_TITLE);
    renderHook(() => useDocumentTitle(""));
    expect(document.title).toBe(BASE_TITLE);
  });
});
