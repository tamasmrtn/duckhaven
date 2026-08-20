import { useNavigate } from "@tanstack/react-router";
import { Ruler } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { useTableSemantics } from "@/queries/semantic";
import { StatusPill } from "./SemanticStatusPill";

/**
 * Which business definitions depend on this table.
 *
 * The direction lineage cannot answer. Lineage knows what feeds `orders`; it
 * does not know that dropping `orders.total_amount` breaks the published
 * definition of revenue that four teams quote in meetings.
 *
 * Takes only the table's address, matching the contract its sibling panels
 * (SnapshotHistoryPanel, TableHealthPanel, LineagePanel) already follow.
 */
export function SemanticPanel({
  ws,
  catalog,
  schema,
  table,
}: {
  ws: string;
  catalog: string;
  schema: string;
  table: string;
}) {
  const navigate = useNavigate();
  const { data, isLoading } = useTableSemantics(ws, catalog, schema, table);

  if (isLoading) {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
      </div>
    );
  }

  const dependents = data?.dependents ?? [];

  if (dependents.length === 0) {
    return (
      <EmptyState
        icon={Ruler}
        title="No semantic definitions use this table"
        description="Nothing in the semantic layer reads from it, so changing its columns will not break a published metric."
      />
    );
  }

  return (
    <div className="space-y-2 p-4">
      <p className="text-xs text-text-tertiary">
        Changing or removing these columns will break the definitions below.
      </p>
      {dependents.map((dep) => (
        <button
          key={`${dep.model}-${dep.kind}-${dep.name}`}
          type="button"
          className="block w-full rounded border border-[var(--border-subtle)] p-3 text-left hover:bg-[var(--bg-elevated)]"
          onClick={() =>
            void navigate({
              to: "/$ws/semantic/$model",
              params: { ws, model: dep.model },
            })
          }
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{dep.label}</span>
            <span className="text-2xs text-text-tertiary">{dep.kind}</span>
            <span className="text-2xs text-text-tertiary">
              in {dep.model_name}
            </span>
            <StatusPill status={dep.model_status} />
          </div>
          {dep.columns.length > 0 && (
            <div className="mt-1 font-mono text-2xs text-text-tertiary">
              reads {dep.columns.join(", ")}
            </div>
          )}
        </button>
      ))}
    </div>
  );
}
