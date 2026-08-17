import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Banner } from "@/components/ui/banner";
import { SqlPreview } from "@/components/app/SqlPreview";
import { ApiError } from "@/api/client";
import { useCompileMetricQuery } from "@/queries/semantic.mutations";
import type { SemanticMetric, SemanticModel } from "@/types/semantic";
import { useMetricDimensions } from "@/queries/semantic";
import { cn } from "@/utils";

/**
 * The SQL a definition actually produces, shown next to the definition itself.
 *
 * This is what makes the layer debuggable rather than a black box. A metric is a
 * promise about arithmetic, and the only way to check a promise like that is to
 * read the statement it compiles to — so it is one click away from the metric,
 * and it comes from the same compile endpoint the assistant calls rather than a
 * reimplementation that could drift from it.
 */
export function MetricSqlPreview({
  ws,
  model,
  metric,
}: {
  ws: string;
  model: SemanticModel;
  metric: SemanticMetric;
}) {
  const compile = useCompileMetricQuery(ws);
  const { data: legal } = useMetricDimensions(ws, model.slug, metric.name);
  const [dimension, setDimension] = useState<string | null>(null);
  const [grain, setGrain] = useState<string | null>(null);

  const timeAxis = model.dimensions.find(
    (d) => d.name === metric.time_dimension,
  );

  useEffect(() => {
    compile.mutate({
      body: {
        model: model.slug,
        metrics: [metric.name],
        dimensions: dimension ? [dimension] : [],
        ...(grain ? { grain: grain as never } : {}),
      },
      publishedOnly: false,
    });
    // Recompiles when the shape being previewed changes; `compile` is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model.slug, metric.name, dimension, grain]);

  const categorical = (legal ?? []).filter((name) => {
    const dim = model.dimensions.find((d) => d.name === name);
    return dim?.kind === "categorical";
  });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-2xs uppercase tracking-wide text-text-tertiary">
          Preview
        </span>
        <Button
          variant={dimension === null ? "default" : "outline"}
          size="sm"
          className="h-6 text-2xs"
          onClick={() => setDimension(null)}
        >
          total
        </Button>
        {categorical.map((name) => (
          <Button
            key={name}
            variant={dimension === name ? "default" : "outline"}
            size="sm"
            className="h-6 text-2xs"
            onClick={() => setDimension(name)}
          >
            by {name}
          </Button>
        ))}
        {timeAxis && (
          <>
            <span className="mx-1 text-text-tertiary">·</span>
            {[null, "month"].map((value) => (
              <Button
                key={value ?? "none"}
                variant={grain === value ? "default" : "outline"}
                size="sm"
                className={cn("h-6 text-2xs")}
                onClick={() => setGrain(value)}
              >
                {value ?? "no grain"}
              </Button>
            ))}
          </>
        )}
      </div>

      {compile.isError ? (
        <Banner>
          {compile.error instanceof ApiError
            ? compile.error.message
            : "This definition could not be compiled."}
        </Banner>
      ) : compile.data ? (
        // `--bg-surface`, matching QueryProfilePage. `SqlPreview` sets
        // `text-text-primary` on its own `<pre>`, so it belongs on a light
        // surface: on `--bg-code`, which stays dark in *both* themes, that
        // renders near-black on near-black at 1.07:1 — invisible.
        <div className="rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-2">
          <SqlPreview sql={compile.data.sql} />
        </div>
      ) : (
        <div className="text-xs text-text-tertiary">Compiling…</div>
      )}

      {compile.data?.warnings?.map((warning) => (
        <p key={warning} className="text-2xs text-text-tertiary">
          {warning}
        </p>
      ))}
    </div>
  );
}
