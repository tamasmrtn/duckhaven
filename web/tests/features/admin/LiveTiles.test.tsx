import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { createWrapper } from "@tests/utils";
import { LiveTiles } from "@/features/admin/monitoring/LiveTiles";
import { AGENTS } from "@/mock/fixtures/agents";
import type { Agent } from "@/types/agent";

const agent = (over: Partial<Agent>): Agent => ({ ...AGENTS[0], ...over });

describe("LiveTiles status tile", () => {
  it("shows a stopped agent as a quiet word, not a red 24px alarm", () => {
    render(
      <LiveTiles
        agent={agent({ status: "unavailable", lifecycle: "terminated" })}
      />,
      {
        wrapper: createWrapper().wrapper,
      },
    );
    const status = screen.getByTestId("live-status");
    expect(status).toHaveTextContent("terminated");
    expect(status).toHaveClass("text-text-secondary", "text-md");
    expect(status).not.toHaveClass("text-[var(--status-failed)]", "text-xl");
  });

  it("keeps a crashed or unreachable agent red", () => {
    render(
      <LiveTiles
        agent={agent({ status: "unavailable", lifecycle: "failed" })}
      />,
      {
        wrapper: createWrapper().wrapper,
      },
    );
    expect(screen.getByTestId("live-status")).toHaveClass(
      "text-[var(--status-failed)]",
    );
  });

  it("keeps a healthy agent green, and the measurements at number size", () => {
    render(
      <LiveTiles agent={agent({ status: "healthy", lifecycle: "running" })} />,
      {
        wrapper: createWrapper().wrapper,
      },
    );
    expect(screen.getByTestId("live-status")).toHaveClass(
      "text-[var(--status-success)]",
    );
    expect(screen.getByTestId("live-queued")).toHaveClass("text-xl");
  });
});
