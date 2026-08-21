import { useQueryProfile } from "@/queries/queries";
import { Skeleton } from "@/components/ui/skeleton";
import { formatBytes } from "@/utils";
import { ProfileSummary } from "./ProfileSummary";
import { ProfileTree } from "./ProfileTree";
import { BADGE_LABELS, isSpilled } from "./highlights";

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center">
      <p className="text-xs text-text-tertiary">{message}</p>
    </div>
  );
}

export function ProfilePanel({
  queryId,
  enabled,
}: {
  queryId: string | null;
  enabled: boolean;
}) {
  const { data: profile, isLoading } = useQueryProfile(queryId, enabled);

  if (!enabled) {
    return (
      <EmptyState message="The profile appears once the query finishes." />
    );
  }
  if (isLoading) {
    return (
      <div className="space-y-1 p-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full animate-shimmer rounded" />
        ))}
      </div>
    );
  }
  if (!profile) {
    return (
      <EmptyState message="No profile for this query (DDL/DML or profiling unavailable)." />
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <ProfileSummary summary={profile.summary} />
      {isSpilled(profile.summary) && (
        <div className="border-b border-[var(--border-subtle)] bg-[var(--status-failed)]/10 px-4 py-1.5 text-2xs text-[var(--status-failed)]">
          {BADGE_LABELS.spill}: {formatBytes(profile.summary.spill_bytes)}{" "}
          spilled
          {profile.summary.reserved_memory_bytes != null
            ? ` over a ${formatBytes(profile.summary.reserved_memory_bytes)} reservation`
            : ""}{" "}
          — give it more memory or reduce intermediate result size.
        </div>
      )}
      <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-1 text-2xs uppercase tracking-wide text-text-tertiary">
        <span className="flex-1">Operator</span>
        <span className="w-40 text-right">Rows (read → out)</span>
        <span className="w-16 text-right">Bytes</span>
        <span className="w-24 text-right">Time</span>
      </div>
      <div className="flex-1 overflow-auto">
        <ProfileTree tree={profile.tree} summary={profile.summary} />
      </div>
    </div>
  );
}
