import { Link } from "@tanstack/react-router";
import { FilePen } from "lucide-react";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { useDashboard, useDashboardProposal } from "@/queries/dashboards";
import type { ChatProposal } from "@/types/assistant";
import type {
  DashboardChange,
  DashboardFile,
  DashboardProposalStatus,
} from "@/types/dashboard";

/** How many changes the card lists before it says how many more there are. */
const LISTED = 5;

const STATUS: Record<DashboardProposalStatus | "gone", string> = {
  pending: "Waiting for you",
  accepted: "Accepted as the draft",
  discarded: "Discarded",
  stale: "Out of date: the dashboard changed since",
  expired: "Expired",
  gone: "Expired",
};

/** A top-level text of a file, such as its title, if it has one. */
function textAt(file: DashboardFile | undefined, key: string): string | null {
  const value = (file as Record<string, unknown> | undefined)?.[key];
  return typeof value === "string" ? value : null;
}

/**
 * A change as a reviewer reads it: what it touches, and how. Given the file
 * before and after, a changed text such as the title says what it was and
 * what it becomes, so it can be judged without opening the file.
 */
export function describeChange(
  change: DashboardChange,
  before?: DashboardFile,
  after?: DashboardFile,
): string {
  const [kind, name] = change.path.split(/\.(.*)/s);
  const what =
    kind === "widgets"
      ? `Widget ${name}`
      : kind === "parameters"
        ? `Filter ${name}`
        : kind === "layout"
          ? "Layout"
          : kind.charAt(0).toUpperCase() + kind.slice(1);
  if (name === undefined) {
    const from = textAt(before, kind);
    const to = textAt(after, kind);
    if (
      change.change === "changed" &&
      from !== null &&
      to !== null &&
      from !== to
    )
      return `${what} changed: “${from}” → “${to}”`;
    if (change.change === "added" && to !== null)
      return `${what} added: “${to}”`;
    if (change.change === "removed" && from !== null)
      return `${what} removed: “${from}”`;
  }
  return `${what} ${change.change}`;
}

/**
 * Changes the assistant proposed to a dashboard. Nothing is saved until the
 * person reviews them, on the proposal's own page, and accepts them there.
 */
export function ChatProposalCard({
  ws,
  card,
}: {
  ws: string;
  card: ChatProposal;
}) {
  const { proposal } = card;
  const { data, error } = useDashboardProposal(ws, proposal.id);
  // The dashboard as writers see it now: `proposal.title` is the title the
  // changes would leave, so naming the dashboard by it reads as if done.
  const { data: base } = useDashboard(ws, proposal.dashboard, true);
  const target = base?.title ?? proposal.dashboard;
  const gone = error instanceof ApiError && error.status === 404;
  const status = gone ? "gone" : data?.status;
  const more = proposal.changes.length - LISTED;
  return (
    <section
      aria-label={`Proposed changes to ${target}`}
      className="flex min-w-0 flex-col gap-2 self-stretch rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2"
    >
      <header className="flex items-start gap-2">
        <FilePen
          className="mt-0.5 size-3.5 shrink-0 text-text-tertiary"
          aria-hidden
        />
        <div className="min-w-0 space-y-0.5">
          <h3 className="truncate text-xs font-medium text-text-secondary">
            Proposed changes to {target}
          </h3>
          {status && (
            <p className="text-2xs text-text-tertiary">{STATUS[status]}</p>
          )}
        </div>
      </header>
      {/* The model's words, shown as text: never as markdown or a link. */}
      <p className="text-xs text-text-primary">{proposal.summary}</p>
      <ul className="space-y-0.5 text-2xs text-text-secondary">
        {proposal.changes.slice(0, LISTED).map((change) => (
          <li key={change.path}>
            {describeChange(change, base?.spec, data?.result_spec)}
          </li>
        ))}
        {more > 0 && (
          <li className="text-text-tertiary">
            and {more} more {more === 1 ? "change" : "changes"}
          </li>
        )}
      </ul>
      {!gone && (
        <div>
          <Button asChild variant="outline" size="sm" className="h-7 text-xs">
            <Link
              to="/$ws/dashboard-proposals/$proposal"
              params={{ ws, proposal: proposal.id }}
            >
              {status === "pending" || status === undefined
                ? "Review changes"
                : "Open"}
            </Link>
          </Button>
        </div>
      )}
    </section>
  );
}
