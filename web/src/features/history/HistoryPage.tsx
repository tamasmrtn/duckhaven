import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, Clock, RefreshCw, Search, X } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
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
import { StatusPill } from "@/components/app/StatusPill";
import { AgentFilterCombobox } from "@/components/app/AgentFilterCombobox";
import { UserFilterCombobox } from "@/components/app/UserFilterCombobox";
import { useWorkspaceQueries } from "@/queries/queries";
import { useAgents } from "@/queries/agents";
import { useMe } from "@/queries/auth";
import { useWorkspaces } from "@/queries/workspaces";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { formatBoundaryDay } from "@/features/admin/metricsTime";
import { DurationCell, SqlCell } from "@/components/app/queryTableCells";
import { cn, shortId } from "@/utils";
import {
  DURATION_UNITS,
  type DurationUnit,
  type HistorySearch,
  RANGE_LABELS,
  STATEMENT_TYPES,
  STATUSES,
  type SortKey,
  TIME_RANGES,
  type TimeRange,
  durationToMs,
  rangeBoundary,
  resolveFilters,
} from "./filters";

const ORIGIN_OPTIONS = [
  { value: "all" as const, label: "All" },
  { value: "interactive" as const, label: "Interactive" },
  { value: "scheduled" as const, label: "Scheduled" },
  { value: "session" as const, label: "Connection" },
];

const PAGE_SIZE = 50;

/**
 * A UTC instant as the local wall-clock string `datetime-local` expects.
 *
 * The input reads and writes local time while the URL holds UTC, so the offset
 * has to be applied on the way out — slicing the ISO string instead shows the
 * UTC hour in a local-time field, and re-editing shifts the value by the offset
 * again each time.
 */
function toLocalInputValue(iso: string | undefined): string {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  return new Date(ms - new Date(ms).getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}

export function HistoryPage() {
  const { ws } = useParams({ from: "/$ws/history" });
  const search = useSearch({ from: "/$ws/history" });
  const navigate = useNavigate();
  const { data: me } = useMe();
  const isAdmin = me?.role === "admin";

  // One clock for the whole view, fixed when it mounts. Two reasons: reading
  // the clock every render would hand the query a new `since` each time, which
  // changes the key, which refetches, which renders; and a window that slid
  // while you paged through it would quietly drop rows off the far end. It also
  // guarantees the boundary the preset menu advertises is the one the request
  // is actually scoped to.
  const [now] = useState(() => Date.now());
  const f = useMemo(() => resolveFilters(search, now), [search, now]);
  const statuses = f.statuses;
  const types = f.types;

  /**
   * Merge one key into the URL rather than replacing the search object.
   *
   * The clobbering form (`search: { agent: id }`) was what this page used for
   * its agent filter, and with more than one parameter in play it would wipe
   * every sibling filter on each change.
   */
  function setFilter(patch: Partial<HistorySearch>) {
    void navigate({
      to: "/$ws/history",
      params: { ws },
      search: (prev) => {
        const next: Record<string, unknown> = { ...prev, ...patch };
        // Defaults and cleared values leave the URL entirely.
        for (const [k, v] of Object.entries(next)) {
          if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) {
            delete next[k];
          }
        }
        return next as HistorySearch;
      },
      replace: true,
    });
  }

  // The search box is local until it settles, so typing does not push a history
  // entry per keystroke.
  const [qDraft, setQDraft] = useState(search.q ?? "");
  const debouncedQ = useDebouncedValue(qDraft, 300);
  useEffect(() => {
    if (debouncedQ !== (search.q ?? ""))
      setFilter({ q: debouncedQ || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);
  // Pull the box back in step when the URL changes underneath it (Clear
  // filters, or a back navigation). Adjusted during render rather than in an
  // effect, which would render once with the stale value and again with the
  // fresh one.
  const [lastQ, setLastQ] = useState(search.q);
  if (search.q !== lastQ) {
    setLastQ(search.q);
    setQDraft(search.q ?? "");
  }

  const [idDraft, setIdDraft] = useState(search.id ?? "");
  const debouncedId = useDebouncedValue(idDraft, 300);
  useEffect(() => {
    if (debouncedId !== (search.id ?? ""))
      setFilter({ id: debouncedId || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedId]);
  const [lastId, setLastId] = useState(search.id);
  if (search.id !== lastId) {
    setLastId(search.id);
    setIdDraft(search.id ?? "");
  }

  // Debounced for the same reason as the two above: typing "1500" would
  // otherwise navigate four times and issue four requests, one per prefix.
  const [slowerDraft, setSlowerDraft] = useState(
    search.slower != null ? String(search.slower) : "",
  );
  const debouncedSlower = useDebouncedValue(slowerDraft, 300);
  useEffect(() => {
    const next = debouncedSlower ? Number(debouncedSlower) : undefined;
    if (next !== search.slower) setFilter({ slower: next });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSlower]);
  const [lastSlower, setLastSlower] = useState(search.slower);
  if (search.slower !== lastSlower) {
    setLastSlower(search.slower);
    setSlowerDraft(search.slower != null ? String(search.slower) : "");
  }

  // Cross-workspace is an admin affordance; a non-admin never sends it. An
  // agent filter spans workspaces (agents are global), so an admin viewing one
  // needs the cross-workspace scope to see all of its runs.
  const all = isAdmin && (search.scope === "all" || !!search.agent);
  // The default scope is "my runs", which cannot be requested until useMe
  // resolves. An explicit `user` in the URL needs no such wait.
  const queryEnabled = search.user != null || !!me;

  const {
    items: rows,
    hasMore,
    isLoading,
    isFetching,
    isError,
    refetch,
    fetchNextPage,
    isFetchingNextPage,
  } = useWorkspaceQueries(ws, {
    all_workspaces: all,
    // Absent `user` means "mine": the default scope. "all" drops the filter.
    user_id:
      search.user == null
        ? me?.id
        : search.user === "all"
          ? undefined
          : search.user,
    origin: search.origin,
    agent_id: search.agent,
    since: f.since,
    until: f.until,
    q: search.q,
    query_id: search.id,
    status: statuses.length ? statuses : undefined,
    statement_type: types.length ? types : undefined,
    slower_than_ms:
      search.slower != null ? durationToMs(search.slower, f.unit) : undefined,
    sort: f.sort,
    dir: f.dir,
    limit: PAGE_SIZE,
    // The default scope needs the caller's own id, so wait for it.
    enabled: queryEnabled,
  });

  const { data: agents = [] } = useAgents();
  const { data: workspaces = [] } = useWorkspaces();
  const agentName = new Map(agents.map((a) => [a.id, a.name]));
  const workspaceSlug = new Map(workspaces.map((w) => [w.id, w.slug]));

  function openProfile(queryId: string) {
    navigate({ to: "/$ws/queries/$queryId", params: { ws, queryId } });
  }

  function toggleSort(key: SortKey) {
    if (f.sort === key) {
      setFilter({ sort: key, dir: f.dir === "desc" ? "asc" : "desc" });
    } else {
      setFilter({ sort: key, dir: "desc" });
    }
  }

  function toggleMulti(
    current: string[],
    value: string,
    key: "status" | "type",
  ) {
    const next = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value];
    setFilter({ [key]: next.length ? next.join(",") : undefined });
  }

  const hasFilters =
    !!search.q ||
    !!search.id ||
    statuses.length > 0 ||
    types.length > 0 ||
    search.slower != null ||
    !!search.origin ||
    !!search.agent ||
    search.range != null ||
    search.user != null;

  // Only worth a column when something in view actually belongs to a session,
  // so the ordinary history table keeps its existing shape.
  const anySession = rows.some((q) => q.session_id);

  const columns: { label: string; sort?: SortKey }[] = all
    ? [
        { label: "Status" },
        { label: "Workspace" },
        { label: "Agent" },
        { label: "User" },
        { label: "SQL" },
        { label: "Rows" },
        { label: "Duration", sort: "duration" },
        { label: "Started", sort: "started_at" },
      ]
    : [
        { label: "Status" },
        { label: "SQL" },
        { label: "Agent" },
        { label: "User" },
        ...(anySession ? [{ label: "Connection" }] : []),
        { label: "Rows" },
        { label: "Duration", sort: "duration" as SortKey },
        { label: "Started", sort: "started_at" as SortKey },
      ];

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="History"
        actions={
          <>
            <div className="ml-auto flex items-center gap-3">
              <AgentFilterCombobox
                value={search.agent ?? null}
                onChange={(id) => setFilter({ agent: id ?? undefined })}
              />
              <Segmented
                label="filter by origin"
                hideLabel
                options={ORIGIN_OPTIONS}
                value={search.origin ?? "all"}
                onChange={(v) =>
                  setFilter({ origin: v === "all" ? undefined : v })
                }
              />
              <button
                type="button"
                onClick={() => void refetch()}
                disabled={isFetching}
                title="Refresh"
                aria-label="Refresh history"
                className="rounded p-1.5 text-text-secondary hover:bg-accent hover:text-text-primary"
              >
                <RefreshCw
                  className={cn("size-3.5", isFetching && "animate-spin")}
                />
              </button>
            </div>
            {isAdmin && (
              <div className="flex items-center gap-3">
                {/* Defaults to the signed-in user, because that is the
                    default scope. Rendering null here showed "All users" while
                    the list was in fact scoped to one — the control and the
                    rows it described disagreed. */}
                <UserFilterCombobox
                  value={
                    search.user === "all"
                      ? null
                      : (search.user ?? me?.id ?? null)
                  }
                  onChange={(id) => setFilter({ user: id ?? "all" })}
                />
                <Segmented
                  label="workspace scope"
                  hideLabel
                  options={[
                    { value: "ws" as const, label: "This workspace" },
                    { value: "all" as const, label: "All workspaces" },
                  ]}
                  value={search.scope === "all" ? "all" : "ws"}
                  onChange={(v) =>
                    setFilter({ scope: v === "all" ? "all" : undefined })
                  }
                />
              </div>
            )}
          </>
        }
      />

      <FilterBar
        search={search}
        qDraft={qDraft}
        setQDraft={setQDraft}
        idDraft={idDraft}
        setIdDraft={setIdDraft}
        slowerDraft={slowerDraft}
        setSlowerDraft={setSlowerDraft}
        statuses={statuses}
        types={types}
        unit={f.unit}
        range={f.range}
        setFilter={setFilter}
        toggleMulti={toggleMulti}
        now={now}
        hasFilters={hasFilters}
        onClear={() =>
          void navigate({
            to: "/$ws/history",
            params: { ws },
            search: {},
            replace: true,
          })
        }
      />

      <div className="flex-1 overflow-auto">
        {/* A disabled TanStack query reports isLoading false, so waiting for
            `me` would otherwise fall through to the empty state and flash
            "No queries match" before the skeletons appear. */}
        {isLoading || !queryEnabled ? (
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
              Could not load history.
            </p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Try again
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <Clock className="size-8 text-text-tertiary" />
            <p className="text-md font-medium text-text-secondary">
              No queries match.
            </p>
            {/* Name the scope rather than leaving an empty table to be
                interpreted — the commonest reason for no rows is the default
                scope, not an absence of history. */}
            <p className="text-sm text-text-tertiary">
              Showing {f.scopeLabel.toLowerCase()}.
            </p>
          </div>
        ) : (
          <>
            <Table containerClassName="overflow-visible" className="text-sm">
              <TableHeader className="sticky top-0 bg-[var(--bg-surface)] z-10">
                <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
                  {columns.map((c) => (
                    <TableHead
                      key={c.label}
                      className="h-auto px-4 py-2 text-left text-xs font-medium text-text-secondary"
                    >
                      {c.sort ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(c.sort!)}
                          aria-label={`sort by ${c.label.toLowerCase()}`}
                          className="inline-flex items-center gap-1 hover:text-text-primary"
                        >
                          {c.label}
                          {f.sort === c.sort &&
                            (f.dir === "desc" ? (
                              <ArrowDown className="size-3" />
                            ) : (
                              <ArrowUp className="size-3" />
                            ))}
                        </button>
                      ) : (
                        c.label
                      )}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((q, i) => (
                  <TableRow
                    key={q.id}
                    onClick={all ? undefined : () => openProfile(q.id)}
                    className={cn(
                      "border-b border-[var(--border-subtle)] hover:bg-accent/50",
                      all ? "" : "cursor-pointer",
                      i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
                    )}
                  >
                    <TableCell className="px-4 py-2">
                      <StatusPill
                        status={q.status}
                        durationMs={q.duration_ms}
                      />
                    </TableCell>
                    {all && (
                      <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary">
                        {workspaceSlug.get(q.workspace_id) ??
                          shortId(q.workspace_id)}
                      </TableCell>
                    )}
                    {!all && <SqlCell query={q} />}
                    <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary">
                      {agentName.get(q.agent_id) ?? shortId(q.agent_id)}
                    </TableCell>
                    <TableCell className="px-4 py-2 text-xs text-text-secondary">
                      {q.user_name ?? "—"}
                    </TableCell>
                    {!all && anySession && (
                      <TableCell className="px-4 py-2 text-xs">
                        {q.session_id ? (
                          <button
                            type="button"
                            aria-label={`connection ${shortId(q.session_id)}`}
                            onClick={(e) => {
                              // The row itself opens the query profile.
                              e.stopPropagation();
                              navigate({
                                to: "/$ws/sessions/$sessionId",
                                params: { ws, sessionId: q.session_id! },
                              });
                            }}
                            className="font-mono text-xs text-text-secondary underline-offset-2 hover:text-text-primary hover:underline"
                          >
                            {shortId(q.session_id)}
                          </button>
                        ) : (
                          <span className="text-text-tertiary">—</span>
                        )}
                      </TableCell>
                    )}
                    {all && <SqlCell query={q} />}
                    <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
                      {q.row_count != null ? q.row_count.toLocaleString() : "—"}
                    </TableCell>
                    <DurationCell query={q} />
                    <TableCell className="px-4 py-2 font-mono text-2xs text-text-tertiary">
                      {new Date(q.started_at).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            <div className="flex flex-col items-center gap-2 py-4">
              {/* What the page actually knows: how many rows it is showing, and
                  whether there are more. Not a total — counting the rows behind
                  a filtered page costs a second aggregate per request. */}
              <span className="text-xs text-text-tertiary font-tabular">
                {`${hasMore ? "Showing" : "All"} ${rows.length} ${
                  rows.length === 1 ? "query" : "queries"
                }`}
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

function FilterBar({
  search,
  qDraft,
  setQDraft,
  idDraft,
  setIdDraft,
  slowerDraft,
  setSlowerDraft,
  statuses,
  types,
  unit,
  range,
  setFilter,
  toggleMulti,
  now,
  hasFilters,
  onClear,
}: {
  search: HistorySearch;
  qDraft: string;
  setQDraft: (v: string) => void;
  idDraft: string;
  setIdDraft: (v: string) => void;
  slowerDraft: string;
  setSlowerDraft: (v: string) => void;
  statuses: string[];
  types: string[];
  unit: DurationUnit;
  range: TimeRange;
  setFilter: (patch: Partial<HistorySearch>) => void;
  toggleMulti: (
    current: string[],
    value: string,
    key: "status" | "type",
  ) => void;
  now: number;
  hasFilters: boolean;
  onClear: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-text-tertiary" />
        <Input
          value={qDraft}
          onChange={(e) => setQDraft(e.target.value)}
          placeholder="Search SQL…"
          aria-label="search SQL"
          className="h-7 w-56 pl-7 text-xs"
        />
      </div>

      <Input
        value={idDraft}
        onChange={(e) => setIdDraft(e.target.value)}
        placeholder="Query ID…"
        aria-label="query ID"
        className="h-7 w-36 text-xs"
      />

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
            onValueChange={(v) =>
              setFilter({
                range: v as TimeRange,
                ...(v === "custom"
                  ? {}
                  : { since: undefined, until: undefined }),
              })
            }
          >
            {TIME_RANGES.map((r) => {
              const boundary = rangeBoundary(r, now);
              return (
                <DropdownMenuRadioItem key={r} value={r} className="text-xs">
                  {RANGE_LABELS[r]}
                  {/* Naming the concrete boundary removes the ambiguity about
                      whether a relative window is calendar days or rolling
                      hours. */}
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

      {range === "custom" && (
        <>
          <input
            type="datetime-local"
            aria-label="from"
            value={toLocalInputValue(search.since)}
            onChange={(e) =>
              setFilter({
                since: e.target.value
                  ? new Date(e.target.value).toISOString()
                  : undefined,
              })
            }
            className="h-7 rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-xs text-text-primary"
          />
          <input
            type="datetime-local"
            aria-label="to"
            value={toLocalInputValue(search.until)}
            onChange={(e) =>
              setFilter({
                until: e.target.value
                  ? new Date(e.target.value).toISOString()
                  : undefined,
              })
            }
            className="h-7 rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-xs text-text-primary"
          />
        </>
      )}

      <MultiSelect
        label="status"
        selected={statuses}
        options={STATUSES}
        onToggle={(v) => toggleMulti(statuses, v, "status")}
      />
      <MultiSelect
        label="statement type"
        selected={types}
        options={STATEMENT_TYPES}
        onToggle={(v) => toggleMulti(types, v, "type")}
      />

      <div className="flex items-center gap-1">
        <Input
          type="number"
          min={0}
          value={slowerDraft}
          onChange={(e) => setSlowerDraft(e.target.value)}
          placeholder="Slower than"
          aria-label="slower than"
          className="h-7 w-28 text-xs"
        />
        <Select
          value={unit}
          onValueChange={(v) => setFilter({ unit: v as DurationUnit })}
        >
          <SelectTrigger
            aria-label="duration unit"
            className="h-7 w-16 text-xs"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DURATION_UNITS.map((u) => (
              <SelectItem key={u} value={u} className="text-xs">
                {u}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Back to the default view — the signed-in user's last 7 days — rather
          than to an unfiltered one. Hidden when nothing is set, so it never
          offers to undo nothing. */}
      {hasFilters && (
        <button
          type="button"
          onClick={onClear}
          className="ml-auto inline-flex items-center gap-1 text-2xs text-text-tertiary hover:text-text-primary"
        >
          <X className="size-3" />
          Clear filters
        </button>
      )}
    </div>
  );
}

/** Multi-select over a fixed set, built on the existing checkbox menu item. */
function MultiSelect({
  label,
  selected,
  options,
  onToggle,
}: {
  label: string;
  selected: string[];
  options: readonly string[];
  onToggle: (value: string) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          aria-label={label}
          className="h-7 text-xs capitalize"
        >
          {selected.length === 0
            ? `Any ${label}`
            : selected.length === 1
              ? selected[0]
              : `${selected.length} ${label}s`}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {options.map((o) => (
          <DropdownMenuCheckboxItem
            key={o}
            checked={selected.includes(o)}
            onCheckedChange={() => onToggle(o)}
            onSelect={(e) => e.preventDefault()}
            className="text-xs capitalize"
          >
            {o}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
