import { useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { StatusPill } from "@/components/app/StatusPill";
import { SqlPreview } from "@/components/app/SqlPreview";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
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
      <PageHeader
        leading={
          <Button variant="ghost" size="icon" asChild>
            <Link
              to="/$ws/history"
              params={{ ws }}
              aria-label="back to history"
            >
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
        }
        title="Query profile"
        badge={
          query && (
            <StatusPill status={query.status} durationMs={query.duration_ms} />
          )
        }
      />

      {query && (
        <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-2 shrink-0">
          <SqlPreview sql={query.sql} maxHeightClassName="max-h-32" />
        </div>
      )}

      {!done ? (
        <Centered message="The profile is available once the query finishes." />
      ) : isLoading ? (
        <div className="space-y-1 px-6 py-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full animate-shimmer rounded" />
          ))}
        </div>
      ) : !profile || !layout ? (
        <Centered message="No profile for this query (DDL/DML or profiling unavailable)." />
      ) : (
        <>
          {query?.cache_status === "hit" && query.result_source_query_id && (
            <div className="border-b border-[var(--border-subtle)] bg-accent px-6 py-1.5 text-2xs text-text-secondary">
              Served from the result cache: nothing ran for this query. This is
              the profile of{" "}
              <Link
                to="/$ws/queries/$queryId"
                params={{ ws, queryId: query.result_source_query_id }}
                className="underline hover:text-text-primary"
              >
                the earlier run
              </Link>{" "}
              whose result it reused.
            </div>
          )}
          <ProfileSummary summary={profile.summary} className="px-6" />
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
