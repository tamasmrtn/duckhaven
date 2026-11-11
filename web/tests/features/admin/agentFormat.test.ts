import { describe, it, expect } from "vitest";
import { agentDotClass } from "@/features/admin/agentFormat";
import type { Agent } from "@/types/agent";

const agent = (over: Partial<Agent>): Agent =>
  ({ status: "unavailable", lifecycle: "running", ...over }) as Agent;

describe("agentDotClass", () => {
  it("shows a crashed agent as red", () => {
    expect(agentDotClass(agent({ lifecycle: "failed" }))).toContain(
      "--status-failed",
    );
  });

  it("shows a cleanly terminated agent as neutral, not as a fault", () => {
    // Both report status "unavailable"; only the lifecycle tells them apart.
    expect(agentDotClass(agent({ lifecycle: "terminated" }))).toContain(
      "--status-cancelled",
    );
  });

  it("shows a transitional agent as amber", () => {
    expect(agentDotClass(agent({ lifecycle: "provisioning" }))).toContain(
      "--status-running",
    );
  });

  it("keeps a healthy agent green", () => {
    expect(agentDotClass(agent({ status: "healthy" }))).toContain(
      "--status-success",
    );
  });
});
