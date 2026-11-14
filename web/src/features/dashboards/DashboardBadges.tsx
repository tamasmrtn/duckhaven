import { GitBranch, PenLine } from "lucide-react";
import { cn } from "@/utils";
import type { DashboardSummary } from "@/types/dashboard";

const PILL =
  "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-medium";

/**
 * Where a dashboard stands: published or a draft, with unpublished changes,
 * and whether it is managed in git or in the app. Each says so in words, with
 * an explanation on hover.
 */
export function DashboardBadges({
  dashboard,
}: {
  dashboard: DashboardSummary;
}) {
  const published = dashboard.status === "published";
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span
        className={cn(
          PILL,
          "text-white",
          published ? "bg-[var(--pill-success)]" : "bg-[var(--pill-queued)]",
        )}
        title={
          published
            ? "Every member of the workspace can see it."
            : "Never published: only writers can see it."
        }
      >
        {published ? "published" : "draft"}
      </span>
      {published && dashboard.has_draft && (
        <span
          className={cn(
            PILL,
            "bg-[var(--status-queued)]/15 text-text-secondary",
          )}
          title="A writer has saved changes that are not published."
        >
          <PenLine className="size-3" aria-hidden />
          unpublished changes
        </span>
      )}
      <span
        className={cn(
          PILL,
          "border border-[var(--border-strong)] text-text-secondary",
        )}
        title={
          dashboard.managed
            ? "Managed in git: changed only by applying its file."
            : "Made and edited in DuckHaven."
        }
      >
        {dashboard.managed && <GitBranch className="size-3" aria-hidden />}
        {dashboard.managed ? "git" : "UI"}
      </span>
    </span>
  );
}
