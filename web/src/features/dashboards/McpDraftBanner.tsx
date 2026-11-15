import { Bot } from "lucide-react";
import { Banner } from "@/components/ui/banner";
import { useMe } from "@/queries/auth";
import type { Dashboard } from "@/types/dashboard";

/** The person whose token the agent held: you, someone by name, or a member. */
function asWhom(d: Dashboard, me: string | undefined): string {
  if (d.updated_by !== null && d.updated_by === me) return "you";
  return d.updated_by_name ?? "another member";
}

/**
 * Says that an agent saved the dashboard's draft over MCP, naming the client
 * it came from and the person it acted as, so whoever reviews the draft knows
 * a person did not write it. Shows nothing once anyone saves it another way.
 */
export function McpDraftBanner({ dashboard }: { dashboard: Dashboard }) {
  const { data: me } = useMe();
  if (dashboard.updated_via !== "mcp" || !dashboard.has_draft) return null;
  return (
    <Banner>
      <Bot className="size-3.5 shrink-0" aria-hidden />
      <span>
        <strong className="font-semibold">Drafted over MCP</strong>
        {dashboard.client && (
          <>
            {" "}
            by <span className="font-mono">{dashboard.client}</span>
          </>
        )}{" "}
        as {asWhom(dashboard, me?.id)}. An agent saved this draft: review it
        before it is published.
      </span>
    </Banner>
  );
}
