import { describe, it, expect } from "vitest";
import { initials, providerLabel } from "@/features/admin/userDisplay";

describe("initials", () => {
  it("tells apart names that share a prefix", () => {
    expect(initials("E2E owner")).toBe("EO");
    expect(initials("E2E reader")).toBe("ER");
  });

  it("splits on hyphens and underscores", () => {
    expect(initials("tpch-bench")).toBe("TB");
    expect(initials("issue101_ci")).toBe("IC");
  });

  it("uses the first two characters of a single word", () => {
    expect(initials("Admin")).toBe("AD");
  });

  it("does not throw on blank input", () => {
    expect(initials("   ")).toBe("?");
  });
});

describe("providerLabel", () => {
  it("renders the service account enum as prose", () => {
    expect(providerLabel("service_account")).toBe("Service account");
  });

  it("capitalises an unknown provider without exposing underscores", () => {
    expect(providerLabel("microsoft")).toBe("Microsoft");
    expect(providerLabel("some_new_idp")).toBe("Some new idp");
  });
});
