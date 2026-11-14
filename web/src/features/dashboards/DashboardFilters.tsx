import { useState } from "react";
import { X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { PageToolbar } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useSemanticModel } from "@/queries/semantic";
import type {
  DimensionParameterSpec,
  ParameterSpec,
  TimeRangeParameterSpec,
} from "@/types/dashboard";
import {
  hasParameters,
  searchKey,
  TIME_PRESETS,
  withoutParameters,
  withParameter,
  type DashboardSearch,
} from "./params";

/** The select value standing for a window the file spells out. */
const DEFAULT = "__default__";
/** The select value standing for "no filter". */
const ALL = "__all__";

type Value = string | number | boolean | null;

const PRESET_LABELS = new Map(TIME_PRESETS);

function TimeRangeFilter({
  name,
  param,
  value,
  onChange,
}: {
  name: string;
  param: TimeRangeParameterSpec;
  value: Value | undefined;
  onChange: (value: Value | undefined) => void;
}) {
  const preset =
    typeof param.default === "string" && PRESET_LABELS.has(param.default)
      ? param.default
      : null;
  // A window spelled out in the file is not one of the presets, so it gets an
  // entry of its own.
  const spelled = param.default != null && preset === null;
  const fallback = preset ?? (spelled ? DEFAULT : ALL);
  const current =
    value === undefined ? fallback : value === null ? ALL : String(value);
  const label = param.label ?? name;
  return (
    <Select
      value={current}
      onValueChange={(v) =>
        // Choosing what the file sets leaves the URL.
        onChange(v === fallback ? undefined : v === ALL ? null : v)
      }
    >
      <SelectTrigger aria-label={label} className="h-7 w-auto gap-1.5 text-xs">
        <span className="text-text-tertiary">{label}:</span>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL} className="text-xs">
          All time
        </SelectItem>
        {spelled && (
          <SelectItem value={DEFAULT} className="text-xs">
            As the dashboard sets it
          </SelectItem>
        )}
        {TIME_PRESETS.map(([key, text]) => (
          <SelectItem key={key} value={key} className="text-xs">
            {text}
          </SelectItem>
        ))}
        {current !== DEFAULT &&
          current !== ALL &&
          !PRESET_LABELS.has(current) && (
            <SelectItem value={current} className="text-xs">
              {current}
            </SelectItem>
          )}
      </SelectContent>
    </Select>
  );
}

function DimensionFilter({
  ws,
  name,
  param,
  value,
  onChange,
}: {
  ws: string;
  name: string;
  param: DimensionParameterSpec;
  value: Value | undefined;
  onChange: (value: Value | undefined) => void;
}) {
  const { data: model } = useSemanticModel(ws, param.model);
  const dimension = model?.dimensions.find((d) => d.name === param.dimension);
  const label = param.label ?? dimension?.display_name ?? name;
  const effective = value === undefined ? (param.default ?? null) : value;
  // Choosing what the file sets leaves the URL; anything else is written there.
  const choose = (next: Value) =>
    onChange(next === (param.default ?? null) ? undefined : next);
  const [draft, setDraft] = useState<string | null>(null);

  const values = dimension?.sample_values ?? [];
  if (values.length === 0) {
    // Nothing to list: type a value instead.
    const commit = () => {
      if (draft === null) return;
      choose(draft.trim() === "" ? null : draft.trim());
      setDraft(null);
    };
    return (
      <label className="flex items-center gap-1.5 text-xs">
        <span className="text-text-tertiary">{label}:</span>
        <Input
          value={draft ?? (effective == null ? "" : String(effective))}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && commit()}
          placeholder="All"
          aria-label={label}
          className="h-7 w-32 text-xs"
        />
      </label>
    );
  }

  const current = effective == null ? ALL : String(effective);
  const options =
    values.includes(current) || current === ALL ? values : [current, ...values];
  return (
    <Select value={current} onValueChange={(v) => choose(v === ALL ? null : v)}>
      <SelectTrigger aria-label={label} className="h-7 w-auto gap-1.5 text-xs">
        <span className="text-text-tertiary">{label}:</span>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL} className="text-xs">
          All
        </SelectItem>
        {options.map((v) => (
          <SelectItem key={v} value={v} className="text-xs">
            {v}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * The dashboard's parameters, in one row above every widget they scope. Each
 * choice is kept in the URL, so a filtered view can be bookmarked and shared.
 */
export function DashboardFilters({
  ws,
  parameters,
  search,
  onChange,
}: {
  ws: string;
  parameters: Record<string, ParameterSpec>;
  search: DashboardSearch;
  onChange: (next: DashboardSearch) => void;
}) {
  const entries = Object.entries(parameters);
  if (entries.length === 0) return null;
  const set = (name: string, value: Value | undefined) =>
    onChange(withParameter(search, name, value));
  return (
    <PageToolbar>
      {entries.map(([name, param]) =>
        param.type === "time_range" ? (
          <TimeRangeFilter
            key={name}
            name={name}
            param={param}
            value={search[searchKey(name)]}
            onChange={(v) => set(name, v)}
          />
        ) : (
          <DimensionFilter
            key={name}
            ws={ws}
            name={name}
            param={param}
            value={search[searchKey(name)]}
            onChange={(v) => set(name, v)}
          />
        ),
      )}
      {hasParameters(search) && (
        <button
          type="button"
          onClick={() => onChange(withoutParameters(search))}
          className="ml-auto inline-flex items-center gap-1 text-2xs text-text-tertiary hover:text-text-primary"
        >
          <X className="size-3" aria-hidden />
          Reset filters
        </button>
      )}
    </PageToolbar>
  );
}
