import { useEffect, useRef } from "react";
import { useCatalogMigrationLogs } from "@/queries/catalog-migrations";
import { cn } from "@/utils";

const LEVEL_COLOR: Record<string, string> = {
  info: "text-text-secondary",
  warning: "text-[var(--brand-orange)]",
  error: "text-red-500",
};

/**
 * Scrollable monospace panel that streams a migration's server-side log lines
 * (polled while the migration is active) and auto-scrolls to the newest line.
 */
export function MigrationLogViewer({
  catalogId,
  migrationId,
  active,
}: {
  catalogId: string;
  migrationId: string;
  active: boolean;
}) {
  const { data: events = [] } = useCatalogMigrationLogs(
    catalogId,
    migrationId,
    active,
  );
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [events.length]);

  return (
    <div
      className="h-48 overflow-auto rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2 font-mono text-xs"
      aria-label="Migration log"
    >
      {events.length === 0 ? (
        <p className="text-text-tertiary">No log output yet…</p>
      ) : (
        events.map((e) => (
          <div
            key={e.seq}
            className={cn("whitespace-pre-wrap", LEVEL_COLOR[e.level])}
          >
            {e.message}
          </div>
        ))
      )}
      <div ref={bottomRef} />
    </div>
  );
}
