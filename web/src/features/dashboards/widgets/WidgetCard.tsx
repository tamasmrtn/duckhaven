import { useState, type ReactNode } from "react";
import { AlertTriangle, Inbox, Info, Lock } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/utils";
import type { RunWidget, WidgetSpec } from "@/types/dashboard";
import { crossFilterParameter } from "../crossFilter";
import type { WidgetSlot } from "../useDashboardRun";
import { ChartWidget } from "./ChartWidget";
import { KpiWidget } from "./KpiWidget";
import { TableWidget } from "./TableWidget";
import { TextWidget } from "./TextWidget";
import {
  Definitions,
  ScopeLine,
  WidgetMenu,
  type WidgetContext,
} from "./WidgetChrome";

function Message({
  icon: Icon,
  tone,
  children,
}: {
  icon: typeof Lock;
  tone?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-start gap-2 py-2 text-xs text-text-secondary">
      <Icon className={cn("mt-0.5 size-4 shrink-0", tone)} aria-hidden />
      <p>{children}</p>
    </div>
  );
}

function Answer({
  spec,
  event,
  asTable,
  context,
}: {
  spec: WidgetSpec;
  event: RunWidget;
  asTable: boolean;
  context: WidgetContext;
}) {
  switch (event.state) {
    case "no_access":
      return (
        <Message icon={Lock}>
          {event.error ??
            "You don't have access to the data this widget reads."}
        </Message>
      );
    case "error":
      return (
        <Message icon={AlertTriangle} tone="text-[var(--status-failed)]">
          {event.error ?? "This widget could not be answered."}
        </Message>
      );
    case "empty":
      return (
        <Message icon={Inbox}>
          No rows for this window and these filters.
        </Message>
      );
  }
  if (spec.type === "kpi") return <KpiWidget event={event} />;
  if (
    !asTable &&
    (event.chart?.type === "line" || event.chart?.type === "bar")
  ) {
    const { select, parameters } = context;
    const parameter = select
      ? crossFilterParameter(spec, event.chart, parameters)
      : null;
    return (
      <ChartWidget
        event={event}
        label={spec.title ?? spec.id}
        selection={
          parameter && select
            ? {
                label: parameters[parameter].label,
                value: parameters[parameter].value,
                onSelect: (value) => select(parameter, value),
              }
            : undefined
        }
      />
    );
  }
  return (
    <TableWidget
      columns={event.rows!.columns}
      data={event.rows!.data}
      truncated={event.rows!.truncated}
      label={spec.title ?? spec.id}
    />
  );
}

function Loading({ type }: { type: WidgetSpec["type"] }) {
  return type === "kpi" ? (
    <div className="space-y-2" aria-hidden>
      <Skeleton className="h-8 w-28" />
      <Skeleton className="h-3 w-40" />
    </div>
  ) : (
    <div className="space-y-2" aria-hidden>
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
    </div>
  );
}

/**
 * One widget on the page: its title, and whatever the run has said about it.
 *
 * Before its answer arrives it shows a placeholder. While a later run is under
 * way it keeps the last answer, dimmed and marked as updating, so refreshing
 * never blanks the page.
 */
export function WidgetCard({
  spec,
  slot,
  context,
}: {
  spec: WidgetSpec;
  /** Undefined for a text widget, which runs nothing. */
  slot?: WidgetSlot;
  context: WidgetContext;
}) {
  const [asTable, setAsTable] = useState(false);
  const heading = `widget-${spec.id}-title`;
  const stale = !!slot?.pending && !!slot.event;
  const event = slot?.event ?? undefined;
  const title = spec.title ?? spec.id;
  let body: ReactNode;
  if (spec.type === "text") {
    body = <TextWidget markdown={spec.markdown ?? ""} />;
  } else if (!slot || (!slot.event && slot.pending)) {
    body = <Loading type={spec.type} />;
  } else if (!slot.event) {
    body = (
      <Message icon={AlertTriangle} tone="text-[var(--status-failed)]">
        This widget was not answered.
      </Message>
    );
  } else {
    body = (
      <Answer
        spec={spec}
        event={slot.event}
        asTable={asTable}
        context={context}
      />
    );
  }

  return (
    <section
      id={`widget-${spec.id}`}
      aria-labelledby={spec.title ? heading : undefined}
      aria-label={spec.title ? undefined : spec.id}
      aria-busy={slot?.pending || undefined}
      data-state={slot?.event?.state ?? (slot?.pending ? "loading" : undefined)}
      className="flex h-full min-w-0 flex-col gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-3"
    >
      {(spec.title || spec.type !== "text") && (
        <header className="flex items-start justify-between gap-2">
          <div className="min-w-0 space-y-0.5">
            {spec.title && (
              <h2
                id={heading}
                className="truncate text-xs font-medium text-text-secondary"
              >
                {spec.title}
              </h2>
            )}
            {spec.description && (
              <p className="text-2xs text-text-tertiary">{spec.description}</p>
            )}
            {event && <ScopeLine spec={spec} event={event} context={context} />}
          </div>
          {spec.type !== "text" && (
            <div className="-mr-1.5 -mt-0.5 flex shrink-0 items-center">
              {stale && (
                <span className="mr-1 text-2xs text-text-tertiary">
                  Updating…
                </span>
              )}
              {event && (
                <Definitions title={title} event={event} ws={context.ws} />
              )}
              <WidgetMenu
                spec={spec}
                event={event}
                context={context}
                asTable={asTable}
                onToggleTable={() => setAsTable((v) => !v)}
              />
            </div>
          )}
        </header>
      )}
      <div className={cn("min-h-0 flex-1", stale && "opacity-60")}>{body}</div>
      {event?.warnings?.map((warning) => (
        <p
          key={warning}
          className="flex items-start gap-1 text-2xs text-text-tertiary"
        >
          <Info className="mt-px size-3 shrink-0" aria-hidden />
          {warning}
        </p>
      ))}
    </section>
  );
}
