import { useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { StatusPill } from "@/components/app/StatusPill";
import { Skeleton } from "@/components/ui/skeleton";
import { useQuery_, useQueryProfile } from "@/queries/queries";
import { ProfileSummary } from "@/features/worksheet/profile/ProfileSummary";
import { ProfileGraph } from "./ProfileGraph";
import { ProfileSidebar } from "./ProfileSidebar";
import { layoutTree } from "./layout";

function Centered({ message }: { message: string }) {
  return (
    <div className="flex flex-1 items-center justify-center px-6 text-center">
      <p className="text-sm text-text-tertiary">{message}</p>
    </div>
  );
}

export function QueryProfilePage() {
  const { ws, queryId } = useParams({ from: "/$ws/queries/$queryId" });
  const { data: query } = useQuery_(queryId);
  const done = query?.status === "done";
  const { data: profile, isLoading } = useQueryProfile(queryId, done);
  const [selectedId, setSelectedId] = useState<string | null>("0");

  const layout = useMemo(
    () => (profile ? layoutTree(profile.tree) : null),
    [profile],
  );

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-2 shrink-0">
        <Link
          to="/$ws/history"
          params={{ ws }}
          className="flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary"
        >
          <ArrowLeft className="size-4" />
          History
        </Link>
        <span className="text-text-tertiary">·</span>
        <span className="text-xs font-medium text-text-secondary shrink-0">
          Query profile
        </span>
        {query && (
          <pre className="min-w-0 flex-1 truncate font-mono text-xs text-text-tertiary">
            {query.sql}
          </pre>
        )}
        {query && (
          <StatusPill status={query.status} durationMs={query.duration_ms} />
        )}
      </div>

      {!done ? (
        <Centered message="The profile is available once the query finishes." />
      ) : isLoading ? (
        <div className="space-y-1 p-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full animate-shimmer rounded" />
          ))}
        </div>
      ) : !profile || !layout ? (
        <Centered message="No profile for this query (DDL/DML or profiling unavailable)." />
      ) : (
        <>
          <ProfileSummary summary={profile.summary} />
          <div className="flex min-h-0 flex-1">
            <div className="min-w-0 flex-1 overflow-hidden bg-[var(--bg-canvas)]">
              <ProfileGraph
                layout={layout}
                summary={profile.summary}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            </div>
            <ProfileSidebar
              layout={layout}
              summary={profile.summary}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          </div>
        </>
      )}
    </div>
  );
}
