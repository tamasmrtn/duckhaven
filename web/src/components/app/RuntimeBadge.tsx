import { Badge } from "@/components/ui/badge";
import type { Agent } from "@/types/agent";
import { cn } from "@/utils";

/**
 * What is noteworthy about an agent's runtime, if anything: a beta or
 * deprecated runtime, or one the control plane won't send work to. A generally
 * available runtime the agent really runs needs no badge.
 */
export function runtimeBadge(
  agent: Agent,
): { label: string; tone: "warn" | "bad" } | null {
  const runtime = agent.runtime;
  if (!runtime) return null;
  if (runtime.state === "unrecognized")
    return { label: "Unsupported", tone: "bad" };
  if (runtime.state === "mismatch") return { label: "Mismatch", tone: "bad" };
  if (runtime.state === "retired" || runtime.status === "retired")
    return { label: "Retired", tone: "bad" };
  if (runtime.status === "beta") return { label: "Beta", tone: "warn" };
  if (runtime.status === "deprecated")
    return { label: "Deprecated", tone: "warn" };
  return null;
}

export function RuntimeBadge({
  agent,
  className,
}: {
  agent: Agent;
  className?: string;
}) {
  const badge = runtimeBadge(agent);
  if (!badge) return null;
  return (
    <Badge
      variant="outline"
      className={cn(
        "px-1.5 py-0 text-2xs font-medium",
        badge.tone === "warn"
          ? "border-[var(--status-running)] text-[var(--status-running)]"
          : "border-[var(--status-failed)] text-[var(--status-failed)]",
        className,
      )}
    >
      {badge.label}
    </Badge>
  );
}
