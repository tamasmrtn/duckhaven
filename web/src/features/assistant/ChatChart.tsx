import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  ChevronDown,
  Info,
  LayoutDashboard,
  Loader2,
  Plus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import "@/features/dashboards/editor/handoff";
import { NewDashboardDialog } from "@/features/dashboards/NewDashboardDialog";
import { Answer } from "@/features/dashboards/widgets/WidgetCard";
import {
  Definitions,
  WidgetMenu,
  type WidgetContext,
} from "@/features/dashboards/widgets/WidgetChrome";
import { useDashboards } from "@/queries/dashboards";
import type { ChatChart, ChatChartWidget } from "@/types/assistant";

/**
 * The dashboards a chart can go on: those edited in the app. A dashboard
 * managed in git changes only through its file there. Mounted while the menu
 * is open, so the list is read only when someone looks for it.
 */
function DashboardChoices({
  ws,
  onPick,
}: {
  ws: string;
  onPick: (name: string) => void;
}) {
  const { data, isLoading, isError } = useDashboards(ws);
  const editable = (data ?? []).filter((d) => !d.managed);
  if (isLoading) {
    return (
      <p className="flex items-center gap-1.5 px-2 py-1.5 text-text-tertiary">
        <Loader2 className="size-3 animate-spin" aria-hidden />
        Reading the dashboards
      </p>
    );
  }
  if (isError) {
    return (
      <p className="px-2 py-1.5 text-text-tertiary">
        The dashboards could not be read.
      </p>
    );
  }
  return editable.map((d) => (
    <DropdownMenuItem key={d.name} onSelect={() => onPick(d.name)}>
      <LayoutDashboard className="size-3.5" aria-hidden />
      <span className="truncate">{d.title}</span>
    </DropdownMenuItem>
  ));
}

/**
 * Put a chart on a dashboard: on one already there, where it opens in the
 * editor at the end of the file, unsaved, to check in the preview; or on a new
 * draft that holds only it.
 */
function AddToDashboard({ ws, chart }: { ws: string; chart: ChatChart }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  function addTo(name: string) {
    void navigate({
      to: "/$ws/dashboards/$dashboard/edit",
      params: { ws, dashboard: name },
      state: {
        addWidget: {
          key: `${chart.tool_call_id}:${Date.now()}`,
          widget: chart.widget,
        },
      },
    });
  }

  return (
    <>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 gap-1.5 text-xs">
            <Plus className="size-3.5" aria-hidden />
            Add to dashboard
            <ChevronDown className="size-3" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56 text-xs">
          {open && <DashboardChoices ws={ws} onPick={addTo} />}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setCreating(true)}>
            <Plus className="size-3.5" aria-hidden />
            New dashboard…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {creating && (
        <NewDashboardDialog
          ws={ws}
          open
          onOpenChange={setCreating}
          widget={chart.widget}
        />
      )}
    </>
  );
}

/**
 * A chart the assistant drew, from the rows its query returned, drawn as a
 * dashboard draws the same widget, with a way to put it on one.
 */
export function ChatChartCard({ ws, chart }: { ws: string; chart: ChatChart }) {
  const [asTable, setAsTable] = useState(false);
  const spec: ChatChartWidget = chart.widget;
  const event = chart.event;
  const title = spec.title ?? spec.id;
  const context = useMemo<WidgetContext>(
    () => ({ ws, dashboard: "assistant", parameters: {}, linkable: false }),
    [ws],
  );
  return (
    <section
      aria-label={`Chart: ${title}`}
      className="flex min-w-0 flex-col gap-2 self-stretch rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2"
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0 space-y-0.5">
          <h3 className="truncate text-xs font-medium text-text-secondary">
            {title}
          </h3>
          {spec.description && (
            <p className="text-2xs text-text-tertiary">{spec.description}</p>
          )}
        </div>
        <div className="-mr-1.5 flex shrink-0 items-center">
          <Definitions title={title} event={event} ws={ws} />
          <WidgetMenu
            spec={spec}
            event={event}
            context={context}
            asTable={asTable}
            onToggleTable={() => setAsTable((v) => !v)}
          />
        </div>
      </header>
      <Answer spec={spec} event={event} asTable={asTable} context={context} />
      {event.warnings?.map((warning) => (
        <p
          key={warning}
          className="flex items-start gap-1 text-2xs text-text-tertiary"
        >
          <Info className="mt-px size-3 shrink-0" aria-hidden />
          {warning}
        </p>
      ))}
      <div>
        <AddToDashboard ws={ws} chart={chart} />
      </div>
    </section>
  );
}
