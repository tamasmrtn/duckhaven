import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, Info, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { useOpenInWorksheet } from "@/features/worksheet/openInWorksheet";
import type { RunWidget, WidgetSpec } from "@/types/dashboard";
import { toCsv } from "../chartData";
import { SKIP_REASONS } from "../params";

/** What a widget needs to know about the page it is on. */
export interface WidgetContext {
  ws: string;
  dashboard: string;
  /** Each parameter's label, kind and value in this run, by name. */
  parameters: Record<
    string,
    { label: string; type: "time_range" | "dimension"; value: unknown }
  >;
}

/**
 * What a widget's numbers cover: its window, the filters applied to it, and
 * any filter on the page that was not, with why. A KPI names its window
 * itself, beside its comparison.
 */
export function ScopeLine({
  spec,
  event,
  context,
}: {
  spec: WidgetSpec;
  event: RunWidget;
  context: WidgetContext;
}) {
  const parts: string[] = [];
  if (spec.type !== "kpi" && event.scope.window) {
    parts.push(event.scope.window.text);
  }
  for (const name of event.scope.applied) {
    const p = context.parameters[name];
    if (p?.type === "dimension" && p.value != null) {
      parts.push(`${p.label}: ${String(p.value)}`);
    }
  }
  // A filter nobody set has nothing to explain.
  const skipped = event.scope.skipped.filter(
    (s) => context.parameters[s.parameter]?.value != null,
  );
  if (parts.length === 0 && skipped.length === 0) return null;
  return (
    <div className="text-2xs text-text-tertiary" data-testid="widget-scope">
      {parts.length > 0 && <p>{parts.join(" · ")}</p>}
      {skipped.map((s) => (
        <p key={s.parameter}>
          Not filtered by{" "}
          {context.parameters[s.parameter]?.label ?? s.parameter}:{" "}
          {SKIP_REASONS[s.reason] ?? s.reason}.
        </p>
      ))}
    </div>
  );
}

/** How the metrics and dimensions a widget reads are defined. */
export function Definitions({
  title,
  event,
  ws,
}: {
  title: string;
  event: RunWidget;
  ws: string;
}) {
  const defs = (event.definitions ?? []).filter(
    (d) => d.kind === "metric" || d.kind === "dimension",
  );
  if (defs.length === 0) return null;
  const models = [...new Set(defs.map((d) => d.model))];
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="size-6 p-0 text-text-tertiary"
          aria-label={`How ${title} is defined`}
        >
          <Info className="size-3.5" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 space-y-3 p-3 text-xs" align="end">
        {defs.map((d) => (
          <div key={`${d.kind}:${d.model}.${d.name}`} className="space-y-1">
            <p className="font-medium text-text-primary">
              {d.label ?? d.name}{" "}
              <span className="font-normal text-text-tertiary">{d.kind}</span>
            </p>
            {d.description && (
              <p className="text-text-secondary">{d.description}</p>
            )}
            {d.expression && (
              <code className="block break-words rounded bg-[var(--bg-surface)] px-1.5 py-1 font-mono text-2xs text-text-primary">
                {d.expression}
              </code>
            )}
            {d.caveat && (
              <p className="flex items-start gap-1 text-text-secondary">
                <AlertTriangle
                  className="mt-0.5 size-3 shrink-0 text-text-tertiary"
                  aria-hidden
                />
                {d.caveat}
              </p>
            )}
          </div>
        ))}
        <p className="border-t border-[var(--border-subtle)] pt-2 text-2xs text-text-tertiary">
          Defined in the semantic layer:{" "}
          {models.map((model, i) => (
            <span key={model}>
              {i > 0 && ", "}
              <Link
                to="/$ws/semantic/$model"
                params={{ ws, model }}
                className="text-[var(--brand-slate-blue)] underline"
              >
                {model}
              </Link>
            </span>
          ))}
        </p>
      </PopoverContent>
    </Popover>
  );
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * A widget's actions: switch between its chart and its rows, see or open the
 * SQL it ran, download its rows, and copy a link to it.
 */
export function WidgetMenu({
  spec,
  event,
  context,
  asTable,
  onToggleTable,
}: {
  spec: WidgetSpec;
  event: RunWidget | undefined;
  context: WidgetContext;
  asTable: boolean;
  onToggleTable: () => void;
}) {
  const [sqlOpen, setSqlOpen] = useState(false);
  const openInWorksheet = useOpenInWorksheet(context.ws);
  const title = spec.title ?? spec.id;
  const chartable =
    (spec.type === "line" || spec.type === "bar") && !!event?.rows;
  const sql = event?.sql;
  const rows = event?.rows;

  function copyLink() {
    const url = new URL(window.location.href);
    url.hash = `widget-${spec.id}`;
    navigator.clipboard.writeText(url.toString()).then(
      () => toast.success("Link copied"),
      () => toast.error("The link could not be copied."),
    );
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="size-6 p-0 text-text-tertiary"
            aria-label={`Actions for ${title}`}
          >
            <MoreHorizontal className="size-3.5" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44 text-xs">
          {chartable && (
            <DropdownMenuItem onSelect={onToggleTable}>
              {asTable ? "View as chart" : "View as table"}
            </DropdownMenuItem>
          )}
          {sql && (
            <DropdownMenuItem onSelect={() => setSqlOpen(true)}>
              View SQL
            </DropdownMenuItem>
          )}
          {sql && (
            <DropdownMenuItem
              onSelect={() => void openInWorksheet({ sql, title })}
            >
              Open in worksheet
            </DropdownMenuItem>
          )}
          {rows && (
            <DropdownMenuItem
              onSelect={() =>
                download(
                  `${context.dashboard}-${spec.id}.csv`,
                  toCsv(rows.columns, rows.data),
                )
              }
            >
              Download CSV
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={copyLink}>Copy link</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {sql && (
        <Dialog open={sqlOpen} onOpenChange={setSqlOpen}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>SQL for {title}</DialogTitle>
              <DialogDescription>
                The query this widget ran, as you, for this view.
              </DialogDescription>
            </DialogHeader>
            <pre className="max-h-96 overflow-auto rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3 font-mono text-xs">
              {sql}
            </pre>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}
