import { useState } from "react";
import { useParams } from "@tanstack/react-router";
import { CheckCircle2, Ruler, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Banner } from "@/components/ui/banner";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Breadcrumb } from "@/components/ui/breadcrumb";
import { EmptyState } from "@/components/app/EmptyState";
import { ApiError } from "@/api/client";
import { useSemanticModel } from "@/queries/semantic";
import {
  usePublishSemanticModel,
  useValidateSemanticModel,
} from "@/queries/semantic.mutations";
import { MetricSqlPreview } from "./MetricSqlPreview";
import { StatusPill, ValidationPill } from "./SemanticStatusPill";
import type { ValidationReport } from "@/types/semantic";

/**
 * One subject area: what it defines, whether those definitions still hold, and
 * whether the assistant is allowed to use them.
 *
 * A `Tabs` strip over the four kinds of definition plus validation, matching the
 * table detail page — the panels are independent and each one answers a
 * different question, so they should not compete for the same scroll.
 */
export function SemanticModelDetail() {
  const { ws, model: slug } = useParams({ from: "/$ws/semantic/$model" });
  const { data: model, isLoading } = useSemanticModel(ws, slug);
  const validate = useValidateSemanticModel(ws, slug);
  const publish = usePublishSemanticModel(ws, slug);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="space-y-2 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!model) {
    return (
      <EmptyState
        icon={Ruler}
        title="No such semantic model"
        description="It may have been deleted, or you may not have access to the tables it binds."
      />
    );
  }

  const imported = model.provider !== "native";

  const runValidate = () => {
    validate.mutate(undefined, {
      onSuccess: (result) => {
        setReport(result);
        toast[result.ok ? "success" : "error"](
          result.ok
            ? "Every definition resolves."
            : `${result.errors.length} definition(s) no longer resolve.`,
        );
      },
    });
  };

  const runPublish = () => {
    publish.mutate(undefined, {
      onSuccess: () => {
        setPublishError(null);
        toast.success("Published. The assistant will use these definitions.");
      },
      // Shown inline rather than in a toast. A publish is refused because
      // several definitions do not resolve, and a list somebody has to read and
      // act on does not belong in something that disappears after four seconds.
      onError: (error) =>
        setPublishError(
          error instanceof ApiError
            ? error.message
            : "Could not publish this model.",
        ),
    });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <div className="min-w-0">
          <Breadcrumb
            items={[
              { label: ws },
              { label: "Semantic models" },
              { label: model.name, emphasis: true },
            ]}
          />
          <div className="mt-1 flex items-center gap-2">
            <h1 className="text-md font-semibold">{model.name}</h1>
            <StatusPill status={model.status} />
          </div>
          {model.description && (
            <p className="mt-0.5 text-xs text-text-tertiary">
              {model.description}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1.5 text-xs"
            disabled={validate.isPending}
            onClick={runValidate}
          >
            <ShieldCheck className="size-3" />
            Validate
          </Button>
          {model.status !== "published" && (
            <Button
              size="sm"
              className="h-7 gap-1.5 text-xs"
              disabled={publish.isPending}
              onClick={runPublish}
            >
              <CheckCircle2 className="size-3" />
              Publish
            </Button>
          )}
        </div>
      </div>

      {imported && (
        <Banner className="mx-6 mt-3">
          Imported from <strong>{model.provider}</strong>. Edit it at the source
          and import again — a model has one owner, which is what keeps the two
          from disagreeing.
        </Banner>
      )}

      {model.status !== "published" && (
        <Banner className="mx-6 mt-3">
          This model is a {model.status}. The assistant will not use its
          definitions until an owner publishes it.
        </Banner>
      )}

      {publishError && <Banner className="mx-6 mt-3">{publishError}</Banner>}

      <Tabs defaultValue="metrics" className="flex min-h-0 flex-1 flex-col">
        <div className="px-6 pt-3">
          <TabsList>
            <TabsTrigger value="metrics">Metrics</TabsTrigger>
            <TabsTrigger value="dimensions">Dimensions</TabsTrigger>
            <TabsTrigger value="datasets">Datasets</TabsTrigger>
            <TabsTrigger value="relationships">Joins</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent
          value="metrics"
          className="min-h-0 flex-1 overflow-auto px-6 py-4"
        >
          {model.metrics.length === 0 ? (
            <EmptyState
              icon={Ruler}
              title="No metrics yet"
              description="A metric is the authoritative answer to a business question — what revenue means here, and how it is calculated."
            />
          ) : (
            <div className="space-y-4">
              {model.metrics.map((metric) => (
                <div
                  key={metric.id}
                  className="rounded border border-[var(--border-subtle)] p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">
                      {metric.display_name ?? metric.name}
                    </span>
                    <code className="rounded bg-[var(--bg-code)] px-1 text-2xs">
                      {metric.name}
                    </code>
                    <StatusPill status={metric.status} />
                    <ValidationPill state={metric.validation_state} />
                  </div>

                  {metric.description && (
                    <p className="mt-1 text-xs text-text-secondary">
                      {metric.description}
                    </p>
                  )}

                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                    <dt className="text-text-tertiary">Calculation</dt>
                    <dd className="font-mono">{metric.expression}</dd>

                    <dt className="text-text-tertiary">Measured on</dt>
                    <dd>
                      {metric.time_dimension ? (
                        <span className="font-mono">
                          {metric.time_dimension}
                        </span>
                      ) : (
                        // Worth saying out loud rather than leaving blank: an
                        // unbound metric is the setup for a time filter landing
                        // on the wrong column.
                        <span className="text-text-tertiary">
                          not bound — time filters cannot use this metric
                        </span>
                      )}
                    </dd>

                    {metric.synonyms.length > 0 && (
                      <>
                        <dt className="text-text-tertiary">Also called</dt>
                        <dd>{metric.synonyms.join(", ")}</dd>
                      </>
                    )}

                    {metric.caveat && (
                      <>
                        <dt className="text-text-tertiary">Caveat</dt>
                        <dd>{metric.caveat}</dd>
                      </>
                    )}
                  </dl>

                  {metric.validation_detail && (
                    <Banner className="mt-2">{metric.validation_detail}</Banner>
                  )}

                  {metric.validation_state !== "broken" && (
                    <div className="mt-3 border-t border-[var(--border-subtle)] pt-3">
                      <MetricSqlPreview ws={ws} model={model} metric={metric} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent
          value="dimensions"
          className="min-h-0 flex-1 overflow-auto px-6 py-4"
        >
          <div className="space-y-2">
            {model.dimensions.map((dim) => (
              <div
                key={dim.id}
                className="rounded border border-[var(--border-subtle)] p-3 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {dim.display_name ?? dim.name}
                  </span>
                  <code className="rounded bg-[var(--bg-code)] px-1 text-2xs">
                    {dim.name}
                  </code>
                  <span className="text-2xs text-text-tertiary">
                    {dim.kind}
                  </span>
                  {dim.is_default_time && (
                    <span className="text-2xs text-text-tertiary">
                      default time axis
                    </span>
                  )}
                  <ValidationPill state={dim.validation_state} />
                </div>
                {dim.description && (
                  <p className="mt-1 text-text-secondary">{dim.description}</p>
                )}
                <div className="mt-1 text-text-tertiary">
                  <span className="font-mono">
                    {dim.dataset}.{dim.expr}
                  </span>
                  {dim.kind === "time" && dim.time_grains.length > 0 && (
                    <> · grains: {dim.time_grains.join(", ")}</>
                  )}
                </div>
                {dim.sample_values.length > 0 && (
                  <div className="mt-1 text-text-tertiary">
                    {/* The reason these exist: "US" vs "United States" is
                        otherwise a silently empty result. */}
                    Values look like: {dim.sample_values.join(", ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </TabsContent>

        <TabsContent
          value="datasets"
          className="min-h-0 flex-1 overflow-auto px-6 py-4"
        >
          <div className="space-y-2">
            {model.datasets.map((ds) => (
              <div
                key={ds.id}
                className="rounded border border-[var(--border-subtle)] p-3 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{ds.name}</span>
                  <ValidationPill state={ds.validation_state} />
                </div>
                <div className="mt-1 font-mono text-text-tertiary">
                  {ds.catalog}.{ds.schema_name}.{ds.table_name}
                </div>
                {ds.primary_key.length > 0 && (
                  <div className="mt-1 text-text-tertiary">
                    Key: {ds.primary_key.join(", ")}
                  </div>
                )}
                {ds.validation_detail && (
                  <Banner className="mt-2">{ds.validation_detail}</Banner>
                )}
              </div>
            ))}
          </div>
        </TabsContent>

        <TabsContent
          value="relationships"
          className="min-h-0 flex-1 overflow-auto px-6 py-4"
        >
          {model.relationships.length === 0 ? (
            <EmptyState
              icon={Ruler}
              title="No joins declared"
              description="Without a declared join, a metric can only be sliced by dimensions on its own table."
            />
          ) : (
            <div className="space-y-2">
              {model.relationships.map((rel) => (
                <div
                  key={rel.id}
                  className="rounded border border-[var(--border-subtle)] p-3 text-xs"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{rel.name}</span>
                    <ValidationPill state={rel.validation_state} />
                  </div>
                  <div className="mt-1 font-mono text-text-tertiary">
                    {rel.left_dataset} → {rel.right_dataset} ({rel.cardinality})
                  </div>
                  <div className="mt-1 text-text-tertiary">
                    on{" "}
                    {rel.join_columns
                      .map(
                        (c) =>
                          `${rel.left_dataset}.${c.left} = ${rel.right_dataset}.${c.right}`,
                      )
                      .join(" and ")}
                  </div>
                  {rel.validation_detail && (
                    <Banner className="mt-2">{rel.validation_detail}</Banner>
                  )}
                </div>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {report && !report.ok && (
        <div className="border-t border-[var(--border-subtle)] px-6 py-3">
          <p className="mb-1 text-xs font-medium text-[var(--status-failed)]">
            Validation found {report.errors.length} problem(s)
          </p>
          <ul className="space-y-0.5 text-xs text-text-secondary">
            {report.errors.map((error) => (
              <li key={`${error.kind}-${error.name}`}>
                <span className="font-mono">{error.name}</span>: {error.detail}
              </li>
            ))}
          </ul>
        </div>
      )}
      {report?.warnings?.map((warning) => (
        <div
          key={warning}
          className="border-t border-[var(--border-subtle)] px-6 py-2 text-xs text-text-tertiary"
        >
          {warning}
        </div>
      ))}
    </div>
  );
}
