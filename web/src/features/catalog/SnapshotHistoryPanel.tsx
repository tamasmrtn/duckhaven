import { useState } from "react";
import { History, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useTableSnapshots } from "@/queries/schemas";
import {
  snapshotByTimestampTemplate,
  snapshotByVersionTemplate,
} from "@/features/catalog/worksheetSql";
import type { TableSnapshot } from "@/types/catalog";

function fmt(n: number | null): string {
  return n == null ? "—" : n.toLocaleString();
}

// Relative-offset convenience picks (Snowflake-style OFFSET), resolved to an
// absolute timestamp client-side and fed to the same AT (TIMESTAMP => …) query.
const RELATIVE_OFFSETS: { label: string; ms: number }[] = [
  { label: "1 hour ago", ms: 3600_000 },
  { label: "1 day ago", ms: 24 * 3600_000 },
  { label: "1 week ago", ms: 7 * 24 * 3600_000 },
];

export function SnapshotHistoryPanel({
  ws,
  catalog,
  schema,
  table,
  onQuery,
}: {
  ws: string;
  catalog: string;
  schema: string;
  table: string;
  /** Opens a new worksheet pre-filled with the given SQL (reuses the table
   * detail's stash→navigate). */
  onQuery: (sql: string) => void;
}) {
  const { data, isLoading } = useTableSnapshots(ws, catalog, schema, table);

  if (isLoading) {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-6 w-full animate-shimmer rounded" />
        <Skeleton className="h-6 w-full animate-shimmer rounded" />
        <Skeleton className="h-6 w-2/3 animate-shimmer rounded" />
      </div>
    );
  }

  const snapshots = data ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* "Query as of…" — timestamp + relative-offset picks (snapshot-id pins
          live on each row below). */}
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-2 shrink-0">
        <span className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Query as of
        </span>
        <QueryAsOfTimestamp
          onQuery={(iso) =>
            onQuery(snapshotByTimestampTemplate(schema, table, iso, catalog))
          }
        />
        {RELATIVE_OFFSETS.map((o) => (
          <Button
            key={o.label}
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => {
              const iso = new Date(Date.now() - o.ms).toISOString();
              onQuery(snapshotByTimestampTemplate(schema, table, iso, catalog));
            }}
          >
            {o.label}
          </Button>
        ))}
      </div>

      {snapshots.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-6 text-sm text-text-tertiary">
          <span className="flex items-center gap-2">
            <History className="size-4" />
            No snapshot history yet.
          </span>
        </div>
      ) : (
        <div className="flex-1 overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-[var(--bg-surface)]">
              <tr className="border-b border-[var(--border-subtle)] text-left">
                <th className="px-3 py-1.5 text-xs font-medium text-text-secondary">
                  Snapshot
                </th>
                <th className="px-3 py-1.5 text-xs font-medium text-text-secondary">
                  Operation
                </th>
                <th className="px-3 py-1.5 text-xs font-medium text-text-secondary">
                  Committed
                </th>
                <th className="px-3 py-1.5 text-right text-xs font-medium text-text-secondary">
                  +rows
                </th>
                <th className="px-3 py-1.5 text-right text-xs font-medium text-text-secondary">
                  −rows
                </th>
                <th className="px-3 py-1.5 text-right text-xs font-medium text-text-secondary">
                  total
                </th>
                <th className="px-3 py-1.5 text-right text-xs font-medium text-text-secondary">
                  files
                </th>
                <th className="px-3 py-1.5" />
              </tr>
            </thead>
            <tbody>
              {snapshots.map((s) => (
                <SnapshotRow
                  key={s.snapshot_id}
                  snap={s}
                  onQuery={() =>
                    onQuery(
                      snapshotByVersionTemplate(
                        schema,
                        table,
                        s.snapshot_id,
                        catalog,
                      ),
                    )
                  }
                />
              ))}
              {/* TODO: a "Restore to this snapshot" row action attaches here
                  once catalog writes (rollback_to_snapshot) are in scope. */}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SnapshotRow({
  snap,
  onQuery,
}: {
  snap: TableSnapshot;
  onQuery: () => void;
}) {
  return (
    <tr className="border-b border-[var(--border-subtle)] hover:bg-[var(--bg-surface-hover)]">
      <td className="px-3 py-1.5 font-mono text-xs text-text-primary">
        {snap.snapshot_id}
        {snap.is_current && (
          <span className="ml-2 rounded border border-[var(--border-subtle)] px-1.5 py-0.5 text-[10px] font-medium text-text-secondary">
            current
          </span>
        )}
      </td>
      <td className="px-3 py-1.5 text-xs text-[var(--brand-maya-blue)]">
        {snap.operation ?? "—"}
      </td>
      <td className="px-3 py-1.5 text-xs text-text-tertiary">
        {new Date(snap.committed_at).toLocaleString()}
      </td>
      <td className="px-3 py-1.5 text-right font-mono text-xs text-text-secondary font-tabular">
        {fmt(snap.added_records)}
      </td>
      <td className="px-3 py-1.5 text-right font-mono text-xs text-text-secondary font-tabular">
        {fmt(snap.deleted_records)}
      </td>
      <td className="px-3 py-1.5 text-right font-mono text-xs text-text-secondary font-tabular">
        {fmt(snap.total_records)}
      </td>
      <td className="px-3 py-1.5 text-right font-mono text-xs text-text-tertiary font-tabular">
        {fmt(snap.total_data_files)}
      </td>
      <td className="px-3 py-1.5 text-right">
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1.5 text-xs"
          onClick={onQuery}
        >
          <ExternalLink className="size-3" />
          Query at this snapshot
        </Button>
      </td>
    </tr>
  );
}

function QueryAsOfTimestamp({ onQuery }: { onQuery: (iso: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="flex items-center gap-1">
      <input
        type="datetime-local"
        aria-label="Query as of timestamp"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-7 rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-xs text-text-primary"
      />
      <Button
        variant="outline"
        size="sm"
        className="h-7 text-xs"
        disabled={!value}
        onClick={() => onQuery(new Date(value).toISOString())}
      >
        Query
      </Button>
    </div>
  );
}
