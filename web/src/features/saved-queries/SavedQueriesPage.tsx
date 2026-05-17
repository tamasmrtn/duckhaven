import { useParams } from "@tanstack/react-router";
import { Database, Clock, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSavedQueries } from "@/queries/queries";

export function SavedQueriesPage() {
  const { ws } = useParams({ from: "/$ws/saved-queries" });
  const { data: queries = [], isLoading } = useSavedQueries(ws);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">Saved queries</h1>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {isLoading ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-32 animate-shimmer rounded-md" />
            ))}
          </div>
        ) : queries.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <Database className="size-8 text-text-tertiary" />
            <p className="text-md font-medium text-text-secondary">
              Save a worksheet to keep it here.
            </p>
            <p className="text-sm text-text-tertiary">
              Click "Save…" in the worksheet editor to name and save your query.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {queries.map((q) => (
              <div
                key={q.id}
                className="flex flex-col gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4 shadow-e1 hover:shadow-e2 transition-shadow"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-sm text-text-primary">
                    {q.name}
                  </p>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-6 shrink-0"
                    aria-label="Open in worksheet"
                  >
                    <ExternalLink className="size-3" />
                  </Button>
                </div>
                <pre className="flex-1 truncate whitespace-pre-wrap font-mono text-xs text-text-secondary bg-[var(--bg-code)] rounded px-2 py-1.5 max-h-16 overflow-hidden">
                  {q.sql}
                </pre>
                {q.last_run_at && (
                  <div className="flex items-center gap-1.5 text-2xs text-text-tertiary">
                    <Clock className="size-3" />
                    Last run {new Date(q.last_run_at).toLocaleDateString()}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
