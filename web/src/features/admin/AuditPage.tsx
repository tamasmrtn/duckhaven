import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import {
  ChevronDown,
  ChevronRight,
  RefreshCw,
  ScrollText,
  X,
} from "lucide-react";
import { PageToolbar } from "@/components/ui/page-header";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { UserFilterCombobox } from "@/components/app/UserFilterCombobox";
import { useAuditEvents } from "@/queries/audit";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { formatBoundaryDay } from "@/features/admin/metricsTime";
import {
  RANGE_LABELS,
  TIME_RANGES,
  type TimeRange,
  rangeBoundary,
  toLocalInputValue,
} from "@/features/history/filters";
import {
  AUDIT_CATEGORIES,
  AUDIT_OUTCOMES,
  type AuditCategory,
  type AuditEvent,
  type AuditOutcome,
} from "@/types/audit";
import { cn, plural, shortId } from "@/utils";
import {
  type AuditSearch,
  CATEGORY_LABELS,
  OUTCOME_LABELS,
  resolveAuditFilters,
} from "./auditFilters";

const PAGE_SIZE = 50;

const COLUMNS = ["When", "By", "Action", "Target", "Affecting"];

/**
 * Admin → Audit: the access audit log, newest first.
 *
 * Every filter lives in the URL, so a narrowed view (one person's changes, the
 * changes people made to their own access) can be bookmarked or sent to a
 * colleague. A row opens to show what changed and where the request came from.
 */
export function AuditPage() {
  const { ws } = useParams({ from: "/$ws/admin/audit" });
  const search = useSearch({ from: "/$ws/admin/audit" });
  const navigate = useNavigate();

  // One clock for the view, fixed when it mounts, so a rolling window does not
  // slide (and change the request) on every render; see HistoryPage.
  const [now] = useState(() => Date.now());
  const f = useMemo(() => resolveAuditFilters(search, now), [search, now]);

  function setFilter(patch: Partial<AuditSearch>) {
    void navigate({
      to: "/$ws/admin/audit",
      params: { ws },
      search: (prev) => {
        const next: Record<string, unknown> = { ...prev, ...patch };
        for (const [k, v] of Object.entries(next)) {
          if (v == null || v === "") delete next[k];
        }
        return next as AuditSearch;
      },
      replace: true,
    });
  }

  // Local until it settles, so typing a slug does not request once per letter.
  const [wsDraft, setWsDraft] = useState(search.workspace ?? "");
  const debouncedWs = useDebouncedValue(wsDraft, 300);
  useEffect(() => {
    if (debouncedWs !== (search.workspace ?? ""))
      setFilter({ workspace: debouncedWs || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedWs]);
  const [lastWs, setLastWs] = useState(search.workspace);
  if (search.workspace !== lastWs) {
    setLastWs(search.workspace);
    setWsDraft(search.workspace ?? "");
  }

  const {
    items,
    hasMore,
    isLoading,
    isFetching,
    isError,
    refetch,
    fetchNextPage,
    isFetchingNextPage,
  } = useAuditEvents({
    since: f.since,
    until: f.until,
    category: search.category,
    outcome: search.outcome,
    actor_id: search.actor,
    subject_id: search.subject,
    workspace: search.workspace,
    self_change: search.own,
    limit: PAGE_SIZE,
  });

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageToolbar>
        <RangeMenu
          range={f.range}
          now={now}
          onChange={(range) =>
            setFilter({
              range,
              ...(range === "custom"
                ? {}
                : { since: undefined, until: undefined }),
            })
          }
        />
        {f.range === "custom" && (
          <>
            <DateTimeInput
              label="from"
              value={search.since}
              onChange={(since) => setFilter({ since })}
            />
            <DateTimeInput
              label="to"
              value={search.until}
              onChange={(until) => setFilter({ until })}
            />
          </>
        )}

        <Select
          value={search.category ?? "all"}
          onValueChange={(v) =>
            setFilter({
              category: v === "all" ? undefined : (v as AuditCategory),
            })
          }
        >
          <SelectTrigger aria-label="category" className="h-7 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all" className="text-xs">
              All events
            </SelectItem>
            {AUDIT_CATEGORIES.map((c) => (
              <SelectItem key={c} value={c} className="text-xs">
                {CATEGORY_LABELS[c]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={search.outcome ?? "all"}
          onValueChange={(v) =>
            setFilter({
              outcome: v === "all" ? undefined : (v as AuditOutcome),
            })
          }
        >
          <SelectTrigger aria-label="outcome" className="h-7 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all" className="text-xs">
              Any outcome
            </SelectItem>
            {AUDIT_OUTCOMES.map((o) => (
              <SelectItem key={o} value={o} className="text-xs">
                {OUTCOME_LABELS[o]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <ToolbarLabel text="By">
          <UserFilterCombobox
            label="filter by who acted"
            allLabel="Anyone"
            value={search.actor ?? null}
            onChange={(id) => setFilter({ actor: id ?? undefined })}
          />
        </ToolbarLabel>
        <ToolbarLabel text="Affecting">
          <UserFilterCombobox
            label="filter by whose access changed"
            allLabel="Anyone"
            value={search.subject ?? null}
            onChange={(id) => setFilter({ subject: id ?? undefined })}
          />
        </ToolbarLabel>

        <Input
          value={wsDraft}
          onChange={(e) => setWsDraft(e.target.value)}
          placeholder="Workspace…"
          aria-label="workspace"
          className="h-7 w-36 text-xs"
        />

        <div className="flex items-center gap-1.5">
          <Checkbox
            id="audit-own-access"
            checked={!!search.own}
            onCheckedChange={(c) => setFilter({ own: c === true || undefined })}
          />
          <label
            htmlFor="audit-own-access"
            className="text-xs text-text-secondary"
          >
            Changes to own access
          </label>
        </div>

        <div className="ml-auto flex items-center gap-3">
          {f.hasFilters && (
            <button
              type="button"
              onClick={() =>
                void navigate({
                  to: "/$ws/admin/audit",
                  params: { ws },
                  search: {},
                  replace: true,
                })
              }
              className="inline-flex items-center gap-1 text-2xs text-text-tertiary hover:text-text-primary"
            >
              <X className="size-3" />
              Clear filters
            </button>
          )}
          <button
            type="button"
            onClick={() => void refetch()}
            disabled={isFetching}
            title="Refresh"
            aria-label="Refresh audit log"
            className="rounded p-1.5 text-text-secondary hover:bg-accent hover:text-text-primary"
          >
            <RefreshCw
              className={cn("size-3.5", isFetching && "animate-spin")}
            />
          </button>
        </div>
      </PageToolbar>

      <div className="scroll-shadow-x flex-1 overflow-auto">
        {isLoading ? (
          <div className="space-y-1 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-12 w-full animate-shimmer rounded"
              />
            ))}
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <p className="text-md font-medium text-text-secondary">
              Could not load the audit log.
            </p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Try again
            </Button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <ScrollText className="size-8 text-text-tertiary" />
            <p className="text-md font-medium text-text-secondary">
              No events match.
            </p>
            <p className="text-sm text-text-tertiary">
              Showing {f.scopeLabel.toLowerCase()}.
            </p>
          </div>
        ) : (
          <>
            <Table
              containerClassName="overflow-visible"
              className="table-gutter text-sm"
            >
              <TableHeader className="sticky top-0 z-10 bg-[var(--bg-surface)]">
                <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
                  {COLUMNS.map((c) => (
                    <TableHead
                      key={c}
                      className="h-auto px-4 py-2 text-left text-xs font-medium text-text-secondary"
                    >
                      {c}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((event, i) => (
                  <EventRows
                    key={event.id}
                    event={event}
                    striped={i % 2 === 1}
                    expanded={expanded.has(event.id)}
                    onToggle={() => toggle(event.id)}
                  />
                ))}
              </TableBody>
            </Table>

            <div className="flex flex-col items-center gap-2 py-4">
              <span className="font-tabular text-xs text-text-tertiary">
                {`${hasMore ? "Showing" : "All"} ${plural(items.length, "event")}`}
              </span>
              {hasMore && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={isFetchingNextPage}
                  onClick={() => void fetchNextPage()}
                >
                  {isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function EventRows({
  event,
  striped,
  expanded,
  onToggle,
}: {
  event: AuditEvent;
  striped: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const when = new Date(event.event_time);
  const target = event.target.name ?? shortId(event.target.id);
  return (
    <Fragment>
      <TableRow
        onClick={onToggle}
        className={cn(
          "cursor-pointer border-b border-[var(--border-subtle)] hover:bg-accent/50",
          striped && "bg-[var(--bg-surface)]/40",
        )}
      >
        <TableCell className="whitespace-nowrap px-4 py-2 font-mono text-xs text-text-tertiary">
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={`${expanded ? "Hide" : "Show"} details: ${event.label}`}
            onClick={(e) => {
              // The row toggles too; without this a click would toggle twice.
              e.stopPropagation();
              onToggle();
            }}
            className="inline-flex items-center gap-1 hover:text-text-primary"
          >
            {expanded ? (
              <ChevronDown className="size-3.5" />
            ) : (
              <ChevronRight className="size-3.5" />
            )}
            <span title={when.toLocaleString()}>
              {when.toLocaleString(undefined, {
                dateStyle: "short",
                timeStyle: "short",
              })}
            </span>
          </button>
        </TableCell>
        <TableCell className="max-w-[14rem] px-4 py-2 text-xs text-text-secondary">
          <div className="truncate" title={actorName(event)}>
            {actorName(event)}
          </div>
          {/* The assistant acts as its own account, for whoever asked; both
              are named so neither is hidden. */}
          {event.actor.on_behalf_of_email && (
            <div
              className="truncate text-text-tertiary"
              title={event.actor.on_behalf_of_email}
            >
              for {event.actor.on_behalf_of_email}
            </div>
          )}
        </TableCell>
        <TableCell className="px-4 py-2 text-xs">
          <div className="flex items-center gap-2 whitespace-nowrap">
            <span className="text-text-primary">{event.label}</span>
            {event.outcome !== "success" && (
              <Pill tone="failed">{OUTCOME_LABELS[event.outcome]}</Pill>
            )}
          </div>
          <div className="font-mono text-2xs text-text-tertiary">
            {event.action}
          </div>
        </TableCell>
        <TableCell className="max-w-[14rem] px-4 py-2 font-mono text-xs">
          <div className="truncate text-text-secondary" title={target}>
            {target}
          </div>
          {event.workspace.slug && (
            <div className="truncate text-2xs text-text-tertiary">
              in {event.workspace.slug}
            </div>
          )}
        </TableCell>
        <TableCell className="px-4 py-2 text-xs text-text-secondary">
          <div className="flex items-center gap-2 whitespace-nowrap">
            {event.subject.email ?? "—"}
            {event.self_change && (
              <Pill
                tone="attention"
                title="The person who made this change is the one whose access it changed."
              >
                Own access
              </Pill>
            )}
          </div>
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
          <TableCell colSpan={COLUMNS.length} className="px-4 pb-4 pt-1">
            <EventDetail event={event} />
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  );
}

/** Who acted, in words, when the actor is not an account. */
function actorName(event: AuditEvent): string {
  if (event.actor.email) return event.actor.email;
  switch (event.actor.type) {
    case "idp":
      return "Identity provider";
    case "system":
      return "DuckHaven";
    case "anonymous":
      return "Not signed in";
    default:
      return shortId(event.actor.id);
  }
}

function EventDetail({ event }: { event: AuditEvent }) {
  const before = event.before ?? {};
  const after = event.after ?? {};
  const changed = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  const details = Object.entries(event.details ?? {});
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(16rem,1fr))] gap-x-8 gap-y-4 rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3 text-xs">
      {changed.length > 0 && (
        <DetailList title="Change">
          {changed.map((key) => (
            <DetailItem key={key} label={humanize(key)}>
              {formatValue(before[key])}
              <span className="px-1.5 text-text-tertiary">→</span>
              {formatValue(after[key])}
            </DetailItem>
          ))}
        </DetailList>
      )}
      {details.length > 0 && (
        <DetailList title="Details">
          {details.map(([key, value]) => (
            <DetailItem key={key} label={humanize(key)}>
              {formatValue(value)}
            </DetailItem>
          ))}
        </DetailList>
      )}
      <DetailList title="Request">
        <DetailItem label="Request ID">{event.request.id ?? "—"}</DetailItem>
        <DetailItem label="Source IP">
          {event.request.source_ip ?? "—"}
        </DetailItem>
        <DetailItem label="User agent">
          {event.request.user_agent ?? "—"}
        </DetailItem>
        <DetailItem label="Signed in with">
          {event.actor.auth_method ?? "—"}
          {event.actor.credential_id && (
            <span className="text-text-tertiary">
              {" "}
              · {shortId(event.actor.credential_id)}
            </span>
          )}
        </DetailItem>
        {event.error_code && (
          <DetailItem label="Error">{event.error_code}</DetailItem>
        )}
      </DetailList>
      <DetailList title="Event">
        <DetailItem label="Event ID">{event.id}</DetailItem>
        <DetailItem label="Time">{event.event_time}</DetailItem>
        <DetailItem label="Target">
          {event.target.type}
          {event.target.id && (
            <span className="text-text-tertiary"> · {event.target.id}</span>
          )}
        </DetailItem>
      </DetailList>
    </div>
  );
}

function DetailList({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-text-tertiary">
        {title}
      </h3>
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1">
        {children}
      </dl>
    </section>
  );
}

function DetailItem({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <>
      <dt className="text-text-tertiary">{label}</dt>
      <dd className="break-words font-mono text-text-primary">{children}</dd>
    </>
  );
}

function humanize(key: string): string {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function formatValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function Pill({
  tone,
  title,
  children,
}: {
  tone: "failed" | "attention";
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-2xs font-medium text-white",
        tone === "failed"
          ? "bg-[var(--pill-failed)]"
          : "bg-[var(--pill-running)]",
      )}
    >
      {children}
    </span>
  );
}

function ToolbarLabel({
  text,
  children,
}: {
  text: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-2xs uppercase tracking-wide text-text-tertiary">
        {text}
      </span>
      {children}
    </div>
  );
}

function RangeMenu({
  range,
  now,
  onChange,
}: {
  range: TimeRange;
  now: number;
  onChange: (range: TimeRange) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          aria-label="time range"
          className="h-7 text-xs"
        >
          {RANGE_LABELS[range]}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuRadioGroup
          value={range}
          onValueChange={(v) => onChange(v as TimeRange)}
        >
          {TIME_RANGES.map((r) => {
            const boundary = rangeBoundary(r, now);
            return (
              <DropdownMenuRadioItem key={r} value={r} className="text-xs">
                {RANGE_LABELS[r]}
                {boundary && (
                  <span className="ml-2 text-text-tertiary">
                    · from {formatBoundaryDay(Date.parse(boundary))}
                  </span>
                )}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function DateTimeInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string | undefined;
  onChange: (iso: string | undefined) => void;
}) {
  return (
    <input
      type="datetime-local"
      aria-label={label}
      value={toLocalInputValue(value)}
      onChange={(e) =>
        onChange(
          e.target.value ? new Date(e.target.value).toISOString() : undefined,
        )
      }
      className="h-7 rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-xs text-text-primary"
    />
  );
}
