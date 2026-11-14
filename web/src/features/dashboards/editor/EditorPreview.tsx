import { useCallback, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Columns3,
  Eye,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Cell, DashboardPreview, WidgetSpec } from "@/types/dashboard";
import { DashboardFilters } from "../DashboardFilters";
import { Row, RunBanners, RunStatus, useWidgetContext } from "../DashboardPage";
import { runParams, withSelection, type DashboardSearch } from "../params";
import { useDashboardRun } from "../useDashboardRun";
import {
  fits,
  moveRow,
  moveWidget,
  setWidth,
  WIDTH_PRESETS,
} from "./yamlEdits";

/** The radio value standing for "no width: share what the row leaves". */
const SHARE = "share";

function Arrange({
  spec,
  index,
  row,
  text,
  onEdit,
  disabled,
}: {
  spec: WidgetSpec;
  index: number;
  row: WidgetSpec[];
  text: string;
  onEdit: (next: string | null) => void;
  disabled: boolean;
}) {
  const widths = row.map((w) => w.width);
  return (
    <div className="mb-1 flex items-center justify-between gap-2 text-2xs text-text-tertiary">
      <span className="truncate font-mono">{spec.id}</span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-1.5 text-2xs"
            disabled={disabled}
            aria-label={`Arrange ${spec.title ?? spec.id}`}
          >
            <Columns3 className="size-3" aria-hidden />
            Arrange
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="text-xs">
          <DropdownMenuItem
            disabled={index === 0}
            onSelect={() => onEdit(moveWidget(text, spec.id, -1))}
          >
            Move left
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={index === row.length - 1}
            onSelect={() => onEdit(moveWidget(text, spec.id, 1))}
          >
            Move right
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="text-2xs text-text-tertiary">
            Width, of 12 columns
          </DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={spec.width == null ? SHARE : String(spec.width)}
            onValueChange={(v) =>
              onEdit(setWidth(text, spec.id, v === SHARE ? null : Number(v)))
            }
          >
            {WIDTH_PRESETS.map(([columns, label]) => (
              <DropdownMenuRadioItem
                key={columns}
                value={String(columns)}
                // A row is 12 columns: a width the rest of the row leaves no
                // room for is not offered.
                disabled={!fits(widths, index, columns)}
              >
                {label} ({columns})
              </DropdownMenuRadioItem>
            ))}
            <DropdownMenuRadioItem value={SHARE}>
              Share what the row leaves
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

/**
 * The file being edited, run as the person editing it. Controls above each
 * widget and row move them and set widths, by changing the file.
 */
export function EditorPreview({
  ws,
  preview,
  current,
  refused,
  text,
  onEdit,
  readOnly,
}: {
  ws: string;
  preview: DashboardPreview | null;
  /** Whether `preview` holds the file as it stands. */
  current: boolean;
  /** Whether the file as it stands does not load. */
  refused: boolean;
  text: string;
  onEdit: (next: string) => void;
  readOnly: boolean;
}) {
  const [search, setSearch] = useState<DashboardSearch>({});
  const spec = preview?.spec;
  const parameters = spec?.parameters;
  // Only what the file declares: a parameter taken out of it is dropped.
  const request = useMemo(
    () => ({
      params: Object.fromEntries(
        Object.entries(runParams(search)).filter(
          ([name]) => !!parameters?.[name],
        ),
      ),
    }),
    [search, parameters],
  );
  const run = useDashboardRun(
    ws,
    preview ? { preview: preview.id, hash: preview.spec_hash } : null,
    request,
  );
  const select = useCallback(
    (name: string, value: Cell) =>
      setSearch((s) => withSelection(s, parameters, name, value)),
    [parameters],
  );
  const context = useWidgetContext(
    ws,
    spec?.name ?? "",
    parameters,
    run,
    select,
  );
  // The controls change the file by widget id and row position, so they
  // wait until the preview holds the file as it stands.
  const locked = readOnly || !current;
  const edit = (next: string | null) => {
    if (next !== null) onEdit(next);
  };

  return (
    <section
      aria-label="Preview"
      className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--border-subtle)] px-4 py-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-text-primary">
          <Eye className="size-3.5" aria-hidden />
          Preview, viewing as you
        </span>
        <span className="text-2xs text-text-tertiary">
          {preview && refused ? (
            "Showing the last file that loaded, until its problems are fixed"
          ) : preview && !current ? (
            <span className="inline-flex items-center gap-1">
              <Loader2 className="size-3 animate-spin" aria-hidden />
              Catching up with the file
            </span>
          ) : (
            <RunStatus run={run} />
          )}
        </span>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto h-7 gap-1.5 text-xs"
          onClick={run.refresh}
          disabled={!preview || run.running}
        >
          <RefreshCw className="size-3.5" aria-hidden />
          Refresh
        </Button>
        <p className="w-full text-2xs text-text-tertiary">
          Every widget runs with your access, so someone with other grants may
          see other numbers.
        </p>
      </div>
      {spec && (
        <DashboardFilters
          ws={ws}
          parameters={parameters ?? {}}
          search={search}
          onChange={setSearch}
        />
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        {!spec ? (
          <p className="p-6 text-xs text-text-tertiary">
            {refused
              ? "The preview appears once the file's problems are fixed."
              : "The preview appears once the file loads."}
          </p>
        ) : (
          <div className="flex flex-col gap-4 p-4">
            <RunBanners run={run} />
            {spec.rows.map((row, r) => (
              <div key={r} className="flex flex-col gap-1">
                {!readOnly && (
                  <div className="flex items-center gap-1 text-2xs text-text-tertiary">
                    <span>Row {r + 1}</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="size-6 p-0"
                      disabled={locked || r === 0}
                      onClick={() => edit(moveRow(text, r, -1))}
                      aria-label={`Move row ${r + 1} up`}
                    >
                      <ArrowUp className="size-3" aria-hidden />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="size-6 p-0"
                      disabled={locked || r === spec.rows.length - 1}
                      onClick={() => edit(moveRow(text, r, 1))}
                      aria-label={`Move row ${r + 1} down`}
                    >
                      <ArrowDown className="size-3" aria-hidden />
                    </Button>
                  </div>
                )}
                <Row
                  row={row}
                  run={run}
                  context={context}
                  arrange={
                    readOnly
                      ? undefined
                      : (w, i) => (
                          <Arrange
                            spec={w}
                            index={i}
                            row={row.widgets}
                            text={text}
                            onEdit={edit}
                            disabled={locked}
                          />
                        )
                  }
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
