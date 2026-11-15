import { Link } from "@tanstack/react-router";
import { FilePen } from "lucide-react";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { useDashboardProposal } from "@/queries/dashboards";
import type { ChatProposal } from "@/types/assistant";
import type {
  DashboardChange,
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

/** A change as a reviewer reads it: what it touches, and how. */
export function describeChange(change: DashboardChange): string {
  const [kind, name] = change.path.split(/\.(.*)/s);
  const what =
    kind === "widgets"
      ? `Widget ${name}`
      : kind === "parameters"
        ? `Filter ${name}`
        : kind === "layout"
          ? "Layout"
          : kind.charAt(0).toUpperCase() + kind.slice(1);
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
  const gone = error instanceof ApiError && error.status === 404;
  const status = gone ? "gone" : data?.status;
  const more = proposal.changes.length - LISTED;
  return (
    <section
      aria-label={`Proposed changes to ${proposal.title}`}
      className="flex min-w-0 flex-col gap-2 self-stretch rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2"
    >
      <header className="flex items-start gap-2">
        <FilePen
          className="mt-0.5 size-3.5 shrink-0 text-text-tertiary"
          aria-hidden
        />
        <div className="min-w-0 space-y-0.5">
          <h3 className="truncate text-xs font-medium text-text-secondary">
            Proposed changes to {proposal.title}
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
          <li key={change.path}>{describeChange(change)}</li>
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
