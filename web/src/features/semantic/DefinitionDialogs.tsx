import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError } from "@/api/client";
import { useCatalogs } from "@/queries/catalogs";
import { useSchemas, useTables } from "@/queries/schemas";
import {
  useAddDataset,
  useAddDimension,
  useAddMetric,
  useAddRelationship,
} from "@/queries/semantic.mutations";
import type { Aggregation, SemanticModel } from "@/types/semantic";

/**
 * Authoring dialogs for the four kinds of definition.
 *
 * Each one is deliberately opinionated about the field that is easiest to get
 * wrong and most expensive to get wrong: a dataset's primary key, a metric's
 * time dimension, a relationship's direction. Those carry explanation next to
 * the input rather than in documentation somebody would have to go and find.
 */

function useSubmitError() {
  const [error, setError] = useState<string | null>(null);
  const report = (err: unknown) =>
    setError(err instanceof ApiError ? err.message : "Something went wrong.");
  return { error, setError, report };
}

/**
 * A labelled control.
 *
 * The label is associated with its input via ``htmlFor``/``id`` rather than
 * merely sitting above it, so a screen reader announces the field the same way
 * a sighted user reads it. Selects are Radix triggers rather than form
 * controls, so those carry their own ``aria-label`` at the call site.
 */
function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label
        htmlFor={htmlFor}
        className="mb-1 block text-xs text-text-secondary"
      >
        {label}
      </Label>
      {children}
      {hint && <p className="mt-1 text-2xs text-text-tertiary">{hint}</p>}
    </div>
  );
}

function Shell({
  open,
  onOpenChange,
  title,
  description,
  error,
  pending,
  onSubmit,
  disabled,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  error: string | null;
  pending: boolean;
  onSubmit: () => void;
  disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">{children}</div>
        {error && (
          <p className="text-xs text-[var(--status-failed)]" role="alert">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button size="sm" disabled={disabled || pending} onClick={onSubmit}>
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function toList(value: string): string[] {
  return value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

export function AddDatasetDialog({
  ws,
  model,
  open,
  onOpenChange,
}: {
  ws: string;
  model: SemanticModel;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const add = useAddDataset(ws, model.slug);
  const { error, setError, report } = useSubmitError();
  const [name, setName] = useState("");
  const [catalog, setCatalog] = useState("");
  const [schema, setSchema] = useState("");
  const [table, setTable] = useState("");
  const [primaryKey, setPrimaryKey] = useState("");

  const { data: catalogs } = useCatalogs(ws);
  const { data: schemas } = useSchemas(ws, catalog || undefined);
  const { data: tables } = useTables(ws, catalog || undefined, schema);

  const submit = () => {
    setError(null);
    add.mutate(
      {
        name,
        catalog,
        schema_name: schema,
        table_name: table,
        primary_key: toList(primaryKey),
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setName("");
          setPrimaryKey("");
          toast.success(`Bound ${name}.`);
        },
        onError: report,
      },
    );
  };

  return (
    <Shell
      open={open}
      onOpenChange={onOpenChange}
      title="Bind a dataset"
      description="A logical table, named in business terms, pointing at a physical one."
      error={error}
      pending={add.isPending}
      disabled={!name || !catalog || !schema || !table}
      onSubmit={submit}
    >
      <Field
        label="Name"
        htmlFor="dataset-name"
        hint="Lowercase letters, digits and underscores. This becomes the table alias in generated SQL."
      >
        <Input
          id="dataset-name"
          value={name}
          placeholder="orders"
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field label="Catalog">
        <Select value={catalog} onValueChange={setCatalog}>
          <SelectTrigger aria-label="Catalog">
            <SelectValue placeholder="Select a catalog" />
          </SelectTrigger>
          <SelectContent>
            {(catalogs ?? []).map((c) => (
              <SelectItem key={c.slug} value={c.slug}>
                {c.slug}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field label="Schema">
        <Select value={schema} onValueChange={setSchema} disabled={!catalog}>
          <SelectTrigger aria-label="Schema">
            <SelectValue placeholder="Select a schema" />
          </SelectTrigger>
          <SelectContent>
            {(schemas ?? []).map((s) => (
              <SelectItem key={s.name} value={s.name}>
                {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field label="Table">
        <Select value={table} onValueChange={setTable} disabled={!schema}>
          <SelectTrigger aria-label="Table">
            <SelectValue placeholder="Select a table" />
          </SelectTrigger>
          <SelectContent>
            {(tables ?? []).map((t) => (
              <SelectItem key={t.name} value={t.name}>
                {t.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field
        label="Primary key"
        htmlFor="dataset-key"
        hint="Comma-separated. Required if anything will join TO this dataset — a join without a key cannot promise one match per row, and would multiply every total that crosses it."
      >
        <Input
          id="dataset-key"
          value={primaryKey}
          placeholder="id"
          onChange={(e) => setPrimaryKey(e.target.value)}
        />
      </Field>
    </Shell>
  );
}

export function AddDimensionDialog({
  ws,
  model,
  open,
  onOpenChange,
}: {
  ws: string;
  model: SemanticModel;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const add = useAddDimension(ws, model.slug);
  const { error, setError, report } = useSubmitError();
  const [name, setName] = useState("");
  const [dataset, setDataset] = useState("");
  const [kind, setKind] = useState<"categorical" | "time">("categorical");
  const [expr, setExpr] = useState("");
  const [synonyms, setSynonyms] = useState("");
  const [samples, setSamples] = useState("");
  const [defaultTime, setDefaultTime] = useState(false);

  const submit = () => {
    setError(null);
    add.mutate(
      {
        name,
        dataset,
        kind,
        expr: expr || null,
        synonyms: toList(synonyms),
        sample_values: toList(samples),
        is_default_time: kind === "time" ? defaultTime : false,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setName("");
          setExpr("");
          setSynonyms("");
          setSamples("");
          toast.success(`Added ${name}.`);
        },
        onError: report,
      },
    );
  };

  return (
    <Shell
      open={open}
      onOpenChange={onOpenChange}
      title="Add a dimension"
      description="A way to slice a number: a categorical attribute, or a time axis."
      error={error}
      pending={add.isPending}
      disabled={!name || !dataset}
      onSubmit={submit}
    >
      <Field label="Name" htmlFor="dimension-name">
        <Input
          id="dimension-name"
          value={name}
          placeholder="country"
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field label="Dataset">
        <Select value={dataset} onValueChange={setDataset}>
          <SelectTrigger aria-label="Dataset">
            <SelectValue placeholder="Select a dataset" />
          </SelectTrigger>
          <SelectContent>
            {model.datasets.map((d) => (
              <SelectItem key={d.id} value={d.name}>
                {d.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field label="Kind">
        <Select
          value={kind}
          onValueChange={(v) => setKind(v as "categorical" | "time")}
        >
          <SelectTrigger aria-label="Kind">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="categorical">Categorical</SelectItem>
            <SelectItem value="time">Time</SelectItem>
          </SelectContent>
        </Select>
      </Field>
      <Field
        label="Expression"
        htmlFor="dimension-expr"
        hint="Defaults to the dimension name."
      >
        <Input
          id="dimension-expr"
          value={expr}
          placeholder="country"
          onChange={(e) => setExpr(e.target.value)}
        />
      </Field>
      <Field
        label="Synonyms"
        htmlFor="dimension-synonyms"
        hint="Comma-separated. The words people actually use — 'nation', 'market'."
      >
        <Input
          id="dimension-synonyms"
          value={synonyms}
          onChange={(e) => setSynonyms(e.target.value)}
        />
      </Field>
      {kind === "time" ? (
        <label className="flex items-center gap-2 text-xs text-text-secondary">
          <Checkbox
            checked={defaultTime}
            onCheckedChange={(v) => setDefaultTime(v === true)}
          />
          Use as this dataset&apos;s default time axis
        </label>
      ) : (
        <Field
          label="Sample values"
          htmlFor="dimension-samples"
          hint="Comma-separated. Lets a question about 'the US' find rows stored as 'United States' instead of returning nothing."
        >
          <Input
            id="dimension-samples"
            value={samples}
            onChange={(e) => setSamples(e.target.value)}
          />
        </Field>
      )}
    </Shell>
  );
}

export function AddMetricDialog({
  ws,
  model,
  open,
  onOpenChange,
}: {
  ws: string;
  model: SemanticModel;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const add = useAddMetric(ws, model.slug);
  const { error, setError, report } = useSubmitError();
  const [name, setName] = useState("");
  const [dataset, setDataset] = useState("");
  const [agg, setAgg] = useState<Aggregation>("sum");
  const [expr, setExpr] = useState("");
  const [filter, setFilter] = useState("");
  const [timeDimension, setTimeDimension] = useState("");
  const [synonyms, setSynonyms] = useState("");
  const [caveat, setCaveat] = useState("");

  const timeDimensions = model.dimensions.filter((d) => d.kind === "time");

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
        synonyms: toList(synonyms),
        caveat: caveat || null,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setName("");
          setExpr("");
          setFilter("");
          setSynonyms("");
          setCaveat("");
          toast.success(
            `Added ${name}. It stays a draft until you publish it.`,
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
      title="Define a metric"
      description="The authoritative answer to a business question, and how it is calculated."
      error={error}
      pending={add.isPending}
      disabled={!name || !dataset || (agg !== "count" && !expr)}
      onSubmit={submit}
    >
      <Field label="Name" htmlFor="metric-name">
        <Input
          id="metric-name"
          value={name}
          placeholder="revenue"
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field label="Dataset">
        <Select value={dataset} onValueChange={setDataset}>
          <SelectTrigger aria-label="Dataset">
            <SelectValue placeholder="Select a dataset" />
          </SelectTrigger>
          <SelectContent>
            {model.datasets.map((d) => (
              <SelectItem key={d.id} value={d.name}>
                {d.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field label="Aggregation">
        <Select value={agg} onValueChange={(v) => setAgg(v as Aggregation)}>
          <SelectTrigger aria-label="Aggregation">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(
              [
                "sum",
                "count",
                "count_distinct",
                "avg",
                "min",
                "max",
              ] as Aggregation[]
            ).map((a) => (
              <SelectItem key={a} value={a}>
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field
        label="Expression"
        htmlFor="metric-expr"
        hint={
          agg === "count"
            ? "Optional for count — leave empty to count rows."
            : "The column or expression to aggregate."
        }
      >
        <Input
          id="metric-expr"
          value={expr}
          placeholder="total_amount"
          onChange={(e) => setExpr(e.target.value)}
        />
      </Field>
      <Field
        label="Filter"
        htmlFor="metric-filter"
        hint="Applied every single time this metric is computed. This is where 'excluding test orders' belongs, so it can never be forgotten."
      >
        <Input
          id="metric-filter"
          value={filter}
          placeholder="status <> 'test'"
          onChange={(e) => setFilter(e.target.value)}
        />
      </Field>
      <Field
        label="Measured on"
        hint="Which date a time filter uses. Without it, a question about 'last month' may silently measure on the wrong column — the most expensive kind of wrong answer, because nothing errors."
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
                  ? "Add a time dimension first"
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
      <Field
        label="Synonyms"
        htmlFor="metric-synonyms"
        hint="Comma-separated — 'turnover', 'GMV'."
      >
        <Input
          id="metric-synonyms"
          value={synonyms}
          onChange={(e) => setSynonyms(e.target.value)}
        />
      </Field>
      <Field
        label="Caveat"
        htmlFor="metric-caveat"
        hint="Shown with every answer this metric produces, at the moment somebody reads the number."
      >
        <Input
          id="metric-caveat"
          value={caveat}
          onChange={(e) => setCaveat(e.target.value)}
        />
      </Field>
    </Shell>
  );
}

export function AddRelationshipDialog({
  ws,
  model,
  open,
  onOpenChange,
}: {
  ws: string;
  model: SemanticModel;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const add = useAddRelationship(ws, model.slug);
  const { error, setError, report } = useSubmitError();
  const [name, setName] = useState("");
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [leftColumn, setLeftColumn] = useState("");
  const [rightColumn, setRightColumn] = useState("");

  const submit = () => {
    setError(null);
    add.mutate(
      {
        name,
        left_dataset: left,
        right_dataset: right,
        join_columns: [{ left: leftColumn, right: rightColumn }],
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setName("");
          setLeftColumn("");
          setRightColumn("");
          toast.success(`Added ${name}.`);
        },
        onError: report,
      },
    );
  };

  return (
    <Shell
      open={open}
      onOpenChange={onOpenChange}
      title="Declare a join"
      description="Always from the fact table toward something unique. There is no other direction: joining toward a non-unique side multiplies rows and inflates every metric that crosses it."
      error={error}
      pending={add.isPending}
      disabled={!name || !left || !right || !leftColumn || !rightColumn}
      onSubmit={submit}
    >
      <Field label="Name" htmlFor="join-name">
        <Input
          id="join-name"
          value={name}
          placeholder="orders_to_customers"
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field
        label="From (the many side)"
        hint="The fact table. Traversal starts here."
      >
        <Select value={left} onValueChange={setLeft}>
          <SelectTrigger aria-label="From (the many side)">
            <SelectValue placeholder="Select a dataset" />
          </SelectTrigger>
          <SelectContent>
            {model.datasets.map((d) => (
              <SelectItem key={d.id} value={d.name}>
                {d.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field
        label="To (the unique side)"
        hint="Must declare a primary key, and the join must be on it."
      >
        <Select value={right} onValueChange={setRight}>
          <SelectTrigger aria-label="To (the unique side)">
            <SelectValue placeholder="Select a dataset" />
          </SelectTrigger>
          <SelectContent>
            {model.datasets
              .filter((d) => d.name !== left)
              .map((d) => (
                <SelectItem key={d.id} value={d.name}>
                  {d.name}
                  {d.primary_key.length === 0 ? " (no key declared)" : ""}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="From column" htmlFor="join-left-col">
          <Input
            id="join-left-col"
            value={leftColumn}
            placeholder="customer_id"
            onChange={(e) => setLeftColumn(e.target.value)}
          />
        </Field>
        <Field label="To column" htmlFor="join-right-col">
          <Input
            id="join-right-col"
            value={rightColumn}
            placeholder="id"
            onChange={(e) => setRightColumn(e.target.value)}
          />
        </Field>
      </div>
    </Shell>
  );
}
