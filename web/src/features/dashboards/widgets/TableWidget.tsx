import { useState } from "react";
import { cn } from "@/utils";
import type { Cell, RunColumn } from "@/types/dashboard";
import { formatTime, formatValue } from "../format";

/** Every row is this tall, so only the rows in view need to be drawn. */
const ROW_PX = 28;
const VIEW_PX = 320;
const OVERSCAN = 8;

const NUMERIC =
  /^(U?(TINYINT|SMALLINT|INTEGER|INT|BIGINT|HUGEINT)|FLOAT|REAL|DOUBLE|DECIMAL.*|NUMERIC.*)$/i;

const TIME = /^(DATE|TIMESTAMP.*)$/i;

function numeric(column: RunColumn): boolean {
  return (
    column.role === "measure" || (!!column.type && NUMERIC.test(column.type))
  );
}

/**
 * A widget's rows as a table. Up to a thousand rows arrive, so only the rows
 * scrolled into view are in the page; the rest are spacers of the same height.
 */
export function TableWidget({
  columns,
  data,
  truncated,
  label,
}: {
  columns: RunColumn[];
  data: Cell[][];
  truncated: boolean;
  label: string;
}) {
  const [top, setTop] = useState(0);
  const first = Math.max(0, Math.floor(top / ROW_PX) - OVERSCAN);
  const count = Math.ceil(VIEW_PX / ROW_PX) + 2 * OVERSCAN;
  const shown = data.slice(first, first + count);
  const after = Math.max(0, data.length - first - shown.length);

  return (
    <div className="flex min-h-0 flex-col gap-1">
      <div
        className="overflow-auto rounded border border-[var(--border-subtle)]"
        style={{ maxHeight: VIEW_PX }}
        onScroll={(e) => setTop(e.currentTarget.scrollTop)}
        role="region"
        aria-label={`Rows of ${label}`}
        tabIndex={0}
      >
        <table
          className="w-full border-collapse text-xs"
          aria-rowcount={data.length + 1}
        >
          <thead className="sticky top-0 z-10 bg-[var(--bg-surface)]">
            <tr aria-rowindex={1}>
              {columns.map((c) => (
                <th
                  key={c.name}
                  scope="col"
                  className={cn(
                    "border-b border-[var(--border-subtle)] px-2 py-1.5 font-medium text-text-secondary whitespace-nowrap",
                    numeric(c) ? "text-right" : "text-left",
                  )}
                >
                  {c.label ?? c.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {first > 0 && <tr aria-hidden style={{ height: first * ROW_PX }} />}
            {shown.map((row, i) => (
              <tr
                key={first + i}
                aria-rowindex={first + i + 2}
                style={{ height: ROW_PX }}
                className="border-b border-[var(--border-subtle)] last:border-0"
              >
                {columns.map((c, j) => (
                  <td
                    key={c.name}
                    className={cn(
                      "px-2 whitespace-nowrap text-text-primary",
                      numeric(c) ? "text-right tabular-nums" : "text-left",
                    )}
                  >
                    {c.role === "measure"
                      ? formatValue(row[j] ?? null, c)
                      : c.role === "time" || TIME.test(c.type ?? "")
                        ? formatTime(row[j] ?? null)
                        : formatCell(row[j])}
                  </td>
                ))}
              </tr>
            ))}
            {after > 0 && <tr aria-hidden style={{ height: after * ROW_PX }} />}
          </tbody>
        </table>
      </div>
      <p className="text-2xs text-text-tertiary">
        {data.length === 1 ? "1 row" : `${data.length.toLocaleString()} rows`}
        {truncated && ", the first the query returned"}
      </p>
    </div>
  );
}

function formatCell(value: Cell | undefined): string {
  if (value === null || value === undefined) return "—";
  return String(value);
}
