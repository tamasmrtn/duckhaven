import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useSemanticModel, useSemanticModels } from "@/queries/semantic";
import { useAddMetric } from "@/queries/semantic.mutations";
import { Field, Shell, useSubmitError } from "./DefinitionDialogs";
import { metricFromSql } from "./metricFromSql";
import type { Aggregation } from "@/types/semantic";

const AGGREGATIONS: Aggregation[] = [
  "sum",
  "count",
  "count_distinct",
  "avg",
  "min",
  "max",
];

/**
 * Promote a worksheet expression into a definition, where it stops being one
 * person's SQL and becomes the answer everybody gets.
 *
 * The gap this closes is not typing. A calculation that lives only in a
 * worksheet is re-derived — slightly differently — every time somebody else
 * needs it, and the assistant re-derives it too. Catching it at the moment it is
 * written, while the author still knows which filter belongs on it and which
 * date column it should be measured on, is the only cheap moment.
 *
 * The two things the worksheet cannot know are asked for rather than guessed:
 * which subject area this belongs to, and which of its datasets the expression
 * reads. Imported models are not offered — they are edited at their source.
 *
 * The form seeds from `sql` once, on mount. Callers must therefore key this
 * component on the selection, so a different expression mounts a fresh form
 * rather than leaving the previous seed in the fields.
 */
export function SaveAsMetricDialog({
  ws,
  sql,
  open,
  onOpenChange,
}: {
  ws: string;
  /** The highlighted expression. */
  sql: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const seeded = useMemo(() => metricFromSql(sql), [sql]);
  const { data: models } = useSemanticModels(ws);
  const [modelSlug, setModelSlug] = useState("");
  const { data: model } = useSemanticModel(ws, modelSlug, Boolean(modelSlug));
  const add = useAddMetric(ws, modelSlug);
  const { error, setError, report } = useSubmitError();

  const [name, setName] = useState(seeded.name);
  const [dataset, setDataset] = useState("");
  const [agg, setAgg] = useState<Aggregation>(seeded.agg);
  const [expr, setExpr] = useState(seeded.expr);
  const [filter, setFilter] = useState("");
  const [timeDimension, setTimeDimension] = useState("");

  const editable = (models ?? []).filter(
    (m) => m.provider === "native" && m.status !== "deprecated",
  );
  const timeDimensions = (model?.dimensions ?? []).filter(
    (d) => d.kind === "time",
  );

  const submit = () => {
    setError(null);
    add.mutate(
      {
        name,
        dataset,
        agg,
        expr: expr || null,
        filter: filter || null,
        time_dimension: timeDimension || null,
        synonyms: [],
        caveat: null,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setFilter("");
          setTimeDimension("");
          toast.success(
            `Added ${name} to ${modelSlug}. It stays a draft until you publish it.`,
          );
        },
        onError: report,
      },
    );
  };

  return (
    <Shell
      open={open}
      onOpenChange={onOpenChange}
      title="Save as metric"
      description="Turn this expression into the authoritative answer, so everyone — and the assistant — computes it the same way."
      error={error}
      pending={add.isPending}
      disabled={!name || !modelSlug || !dataset || (agg !== "count" && !expr)}
      submitLabel="Save"
      onSubmit={submit}
    >
      {editable.length === 0 ? (
        <p className="text-xs text-text-secondary">
          There is no model to save into yet. Create one under Semantic first —
          a metric belongs to a subject area, which is also what binds it to a
          table.
        </p>
      ) : (
        <>
          <Field label="Model">
            <Select
              value={modelSlug}
              onValueChange={(v) => {
                setModelSlug(v);
                setDataset("");
                setTimeDimension("");
              }}
            >
              <SelectTrigger aria-label="Model">
                <SelectValue placeholder="Select a model" />
              </SelectTrigger>
              <SelectContent>
                {editable.map((m) => (
                  <SelectItem key={m.id} value={m.slug}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field
            label="Dataset"
            hint="Which of the model's tables this expression reads."
          >
            <Select
              value={dataset}
              onValueChange={setDataset}
              disabled={!model}
            >
              <SelectTrigger aria-label="Dataset">
                <SelectValue placeholder="Select a dataset" />
              </SelectTrigger>
              <SelectContent>
                {(model?.datasets ?? []).map((d) => (
                  <SelectItem key={d.id} value={d.name}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="Name" htmlFor="save-metric-name">
            <Input
              id="save-metric-name"
              value={name}
              placeholder="revenue"
              onChange={(e) => setName(e.target.value)}
            />
          </Field>

          <Field label="Aggregation">
            <Select value={agg} onValueChange={(v) => setAgg(v as Aggregation)}>
              <SelectTrigger aria-label="Aggregation">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {AGGREGATIONS.map((a) => (
                  <SelectItem key={a} value={a}>
                    {a}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field
            label="Expression"
            htmlFor="save-metric-expr"
            hint="What the aggregation is applied to — read out of the selection, so check it."
          >
            <Input
              id="save-metric-expr"
              value={expr}
              placeholder="total_amount"
              onChange={(e) => setExpr(e.target.value)}
            />
          </Field>

          <Field
            label="Filter"
            htmlFor="save-metric-filter"
            hint="Applied every time the metric is used — this is where 'excludes test orders' belongs."
          >
            <Input
              id="save-metric-filter"
              value={filter}
              placeholder="status <> 'test'"
              onChange={(e) => setFilter(e.target.value)}
            />
          </Field>

          <Field
            label="Measured on"
            hint="The date this metric is counted by. Without it, no time filter can use the metric."
          >
            <Select
              value={timeDimension}
              onValueChange={setTimeDimension}
              disabled={timeDimensions.length === 0}
            >
              <SelectTrigger aria-label="Measured on">
                <SelectValue
                  placeholder={
                    timeDimensions.length === 0
                      ? "No time dimension in this model"
                      : "Select a time dimension"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {timeDimensions.map((d) => (
                  <SelectItem key={d.id} value={d.name}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </>
      )}
    </Shell>
  );
}
