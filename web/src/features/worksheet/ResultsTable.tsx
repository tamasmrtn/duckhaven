import { useMemo, useState, type ReactNode, type UIEvent } from "react";
import {
  useTable,
  tableFeatures,
  rowSortingFeature,
  createSortedRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import {
  Download,
  Copy,
  AlertCircle,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  Sparkles,
  Table2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import type { ColumnSchema, QueryRow } from "@/types/query";
import { cn } from "@/utils";

interface ResultsTableProps {
  columns: string[];
  rows: QueryRow[];
  total: number;
  error?: string | null;
  isLoading?: boolean;
  onLoadMore?: () => void;
  hasMore?: boolean;
  isLoadingMore?: boolean;
  columnSchema?: ColumnSchema[] | null;
  // Undefined outside the worksheet (e.g. CatalogPage's read-only sample
  // preview reuses this component too) — the button only renders when set.
  onFixWithAssistant?: () => void;
  // Extra controls beside the error, e.g. switching to a reachable agent.
  errorActions?: ReactNode;
}

// DuckDB's numeric logical types, as the agent spells them in the column schema.
const NUMERIC_TYPE =
  /^(U?(TINYINT|SMALLINT|INTEGER|INT|BIGINT|HUGEINT)|FLOAT|REAL|DOUBLE|DECIMAL(\(\d+,\s*\d+\))?|NUMERIC.*)$/i;

/** Whether a column holds numbers, so it can be right-aligned like a spreadsheet. */
export function isNumericColumn(
  type: string | undefined,
  rows: QueryRow[],
  column: string,
): boolean {
  if (type) return NUMERIC_TYPE.test(type.trim());
  // No schema (an older agent): judge by the loaded values.
  let seen = false;
  for (const row of rows.slice(0, 50)) {
    const v = row[column];
    if (v === null || v === undefined) continue;
    if (typeof v !== "number") return false;
    seen = true;
  }
  return seen;
}

function cellDisplay(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function copyValue(value: string) {
  void navigator.clipboard.writeText(value);
}

// Declared once, statically, per the v9 migration guide — passed into
// useTable rather than rebuilt on every render.
const tableFeatureSet = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
});

function downloadCsv(columns: string[], rows: QueryRow[]) {
  const header = columns.join(",");
  const body = rows
    .map((row) => columns.map((c) => JSON.stringify(row[c] ?? "")).join(","))
    .join("\n");
  const csv = `${header}\n${body}`;
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "results.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export function ResultsTable({
  columns,
  rows,
  total,
  error,
  isLoading,
  onLoadMore,
  hasMore,
  isLoadingMore,
  columnSchema,
  onFixWithAssistant,
  errorActions,
}: ResultsTableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);

  const typeByColumn = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of columnSchema ?? []) map.set(c.name, c.type);
    return map;
  }, [columnSchema]);

  function handleScroll(e: UIEvent<HTMLDivElement>) {
    if (!onLoadMore || !hasMore || isLoadingMore) return;
    const el = e.currentTarget;
    // Fetch the next page once scrolled within 200px of the bottom.
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 200) {
      onLoadMore();
    }
  }
  const numeric = useMemo(
    () =>
      new Set(
        columns.filter((c) => isNumericColumn(typeByColumn.get(c), rows, c)),
      ),
    [columns, typeByColumn, rows],
  );
  const colDefs: ColumnDef<typeof tableFeatureSet, QueryRow>[] = columns.map(
    (col) => ({
      accessorKey: col,
      header: col,
      cell: ({ getValue }) => {
        const raw = getValue();
        const display = cellDisplay(raw);
        const isNull = raw === null || raw === undefined;
        return (
          <button
            type="button"
            onClick={() => copyValue(display)}
            className={cn(
              "block w-full truncate font-mono text-xs font-tabular",
              numeric.has(col) ? "text-right" : "text-left",
              isNull ? "text-text-tertiary italic" : "text-text-primary",
            )}
            title={display}
            aria-label={`Copy ${display}`}
          >
            {display}
          </button>
        );
      },
    }),
  );

  const table = useTable({
    features: tableFeatureSet,
    data: rows,
    columns: colDefs,
    state: { sorting },
    onSortingChange: setSorting,
  });

  if (error) {
    return (
      <div className="flex h-full flex-col gap-2 overflow-auto p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-[var(--status-failed)]">
            <AlertCircle className="size-5 shrink-0" />
            <p className="text-sm font-medium">Query failed</p>
          </div>
          <div className="flex items-center gap-1.5">
            {errorActions}
            {onFixWithAssistant && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 text-xs"
                onClick={onFixWithAssistant}
              >
                <Sparkles className="size-3.5" />
                Fix with Assistant
              </Button>
            )}
          </div>
        </div>
        <pre
          role="alert"
          className="w-full whitespace-pre-wrap break-words rounded-md border border-[var(--status-failed)]/30 bg-[var(--status-failed)]/10 px-4 py-3 text-left font-mono text-sm text-[var(--status-failed)] select-text"
        >
          {error}
        </pre>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--border-subtle)]">
          <Skeleton className="h-4 w-40 animate-shimmer rounded" />
        </div>
        <div className="flex-1 p-3 space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-5 w-full animate-shimmer rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (columns.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState icon={Table2} title="No results yet" />
      </div>
    );
  }

  const isPartialSort = sorting.length > 0 && rows.length < total;
  // Export/copy must reflect the active sort, not the pre-sort fetch order.
  const sortedRows = table.getRowModel().rows.map((r) => r.original);

  return (
    <div className="flex h-full flex-col">
      {/* Header bar */}
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-3 py-2 shrink-0">
        <span
          className="text-xs text-text-secondary font-tabular"
          title={
            isPartialSort
              ? "Sort only applies to the rows loaded so far — load the rest to sort the full result."
              : undefined
          }
        >
          {isPartialSort
            ? `Sorted: ${rows.length} of ${total} loaded`
            : `${rows.length} / ${total} rows`}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs"
            onClick={() =>
              void navigator.clipboard.writeText(
                [
                  columns.join("\t"),
                  ...sortedRows.map((r) =>
                    columns.map((c) => r[c] ?? "").join("\t"),
                  ),
                ].join("\n"),
              )
            }
          >
            <Copy className="size-3" />
            Copy
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs"
            onClick={() => downloadCsv(columns, sortedRows)}
          >
            <Download className="size-3" />
            Download CSV
          </Button>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto" onScroll={handleScroll}>
        {/* Columns size to their content (up to a cap) instead of splitting
            the width evenly; the table still fills the pane when narrow. */}
        <table className="w-max min-w-full text-sm">
          <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
            {table.getHeaderGroups().map((hg) => (
              <tr
                key={hg.id}
                className="border-b border-[var(--border-subtle)]"
              >
                <th
                  aria-label="Row number"
                  className="sticky left-0 z-10 w-10 bg-[var(--bg-surface)] px-2"
                />
                {hg.headers.map((h) => {
                  const sortDir = h.column.getIsSorted();
                  const type = typeByColumn.get(h.column.id);
                  const right = numeric.has(h.column.id);
                  return (
                    <th
                      key={h.id}
                      className={cn(
                        "whitespace-nowrap px-3 py-1.5 text-xs font-medium text-text-secondary",
                        right ? "text-right" : "text-left",
                      )}
                    >
                      <button
                        type="button"
                        onClick={h.column.getToggleSortingHandler()}
                        className={cn(
                          "flex items-center gap-1",
                          right && "ml-auto",
                        )}
                      >
                        <span>
                          {flexRender(
                            h.column.columnDef.header,
                            h.getContext(),
                          )}
                        </span>
                        {sortDir === "asc" ? (
                          <ArrowUp className="size-3" />
                        ) : sortDir === "desc" ? (
                          <ArrowDown className="size-3" />
                        ) : (
                          <ArrowUpDown className="size-3 text-text-tertiary" />
                        )}
                      </button>
                      {type && (
                        <span
                          className="block font-mono text-2xs font-normal text-[var(--brand-maya-blue)]"
                          title={type}
                        >
                          {type}
                        </span>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row, i) => (
              <tr
                key={row.id}
                className={cn(
                  "border-b border-[var(--border-subtle)] hover:bg-accent/50",
                  i % 2 === 0 ? "bg-transparent" : "bg-[var(--bg-surface)]/50",
                )}
              >
                {/* A header cell, not data: copy, CSV and anything reading the
                    grid's <td>s see only the result's own columns. */}
                <th
                  scope="row"
                  className="sticky left-0 w-10 select-none bg-[var(--bg-canvas)] px-2 text-right font-mono text-2xs font-normal text-text-tertiary"
                >
                  {i + 1}
                </th>
                {row.getAllCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-1 max-w-[320px]">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {isLoadingMore && (
          <div className="py-2 text-center text-xs text-text-tertiary">
            Loading more…
          </div>
        )}
      </div>
    </div>
  );
}
