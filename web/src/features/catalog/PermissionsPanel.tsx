import { useState } from "react";
import { Trash2 } from "lucide-react";
import { ApiError } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCatalogGrants,
  useDeleteGrant,
  useSetAccessMode,
  useUpsertGrant,
} from "@/queries/grants";
import type { Grant, GrantTier } from "@/types/grant";

const TIERS: GrantTier[] = ["metadata", "reader", "writer"];

/** Where a grant sits relative to the object being viewed. */
function inheritedFrom(g: Grant): string {
  if (g.schema_name == null) return "catalog";
  return g.schema_name;
}

function GrantRow({
  grant,
  onRemove,
  inherited,
}: {
  grant: Grant;
  onRemove?: () => void;
  inherited?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 rounded border border-[var(--border-subtle)] px-3 py-1.5 text-sm">
      <span className="min-w-0 flex-1 truncate">
        {grant.user_name ?? grant.user_id}
      </span>
      {inherited && (
        <span className="text-xs text-text-tertiary">
          inherited from {inheritedFrom(grant)}
        </span>
      )}
      <Badge variant="secondary">{grant.tier}</Badge>
      {onRemove && (
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          aria-label="Remove grant"
          onClick={onRemove}
        >
          <Trash2 className="size-3.5" />
        </Button>
      )}
    </div>
  );
}

/**
 * Databricks-style permissions surface for one catalog / schema / table node.
 * Direct grants at this exact level are editable; grants inherited from a
 * coarser level are shown read-only. The open↔scoped toggle appears only at the
 * catalog level, since access mode is a per-catalog attachment setting.
 */
export function PermissionsPanel({
  ws,
  catalog,
  schema,
  table,
}: {
  ws: string;
  catalog: string;
  schema?: string;
  table?: string;
}) {
  const { data, isLoading, error } = useCatalogGrants(ws, catalog);
  const setMode = useSetAccessMode(ws, catalog);
  const upsert = useUpsertGrant(ws, catalog);
  const remove = useDeleteGrant(ws, catalog);
  const [principal, setPrincipal] = useState("");
  const [tier, setTier] = useState<GrantTier>("reader");
  const [formError, setFormError] = useState<string | null>(null);

  const isCatalogScope = !schema && !table;
  const scopeSchema = schema ?? null;
  const scopeTable = table ?? null;

  if (isLoading) return <Skeleton className="h-40 w-full" />;

  if (error) {
    const msg =
      error instanceof ApiError && error.status === 403
        ? "Only workspace owners can view or manage permissions."
        : "Could not load permissions.";
    return <p className="p-4 text-sm text-text-tertiary">{msg}</p>;
  }

  const grants = data?.grants ?? [];
  const scoped = data?.access_mode === "scoped";

  const direct = grants.filter(
    (g) =>
      (g.schema_name ?? null) === scopeSchema &&
      (g.table_name ?? null) === scopeTable,
  );
  // Ancestors: for a table, catalog- and schema-level grants; for a schema,
  // catalog-level grants; for a catalog, nothing.
  const inherited = table
    ? grants.filter(
        (g) =>
          (g.schema_name == null && g.table_name == null) ||
          (g.schema_name === schema && g.table_name == null),
      )
    : schema
      ? grants.filter((g) => g.schema_name == null && g.table_name == null)
      : [];

  async function addGrant() {
    setFormError(null);
    if (!principal) {
      setFormError("Pick a principal first.");
      return;
    }
    try {
      await upsert.mutateAsync({
        user_id: principal,
        schema_name: scopeSchema,
        table_name: scopeTable,
        tier,
      });
      setPrincipal("");
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Could not save grant.");
    }
  }

  return (
    <div className="space-y-5 p-4">
      {isCatalogScope && (
        <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--border-subtle)] p-3">
          <div>
            <p className="text-sm font-medium">Access mode</p>
            <p className="text-xs text-text-tertiary">
              {scoped
                ? "Scoped — members see only what they are granted below."
                : "Open — the workspace role governs everything; grants are not enforced."}
            </p>
          </div>
          <Select
            value={data?.access_mode ?? "open"}
            onValueChange={(v) => setMode.mutate(v as "open" | "scoped")}
          >
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="open">Open</SelectItem>
              <SelectItem value="scoped">Scoped</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}

      <div className="space-y-2">
        <p className="text-sm font-medium">Grants</p>
        {direct.length === 0 ? (
          <p className="text-xs text-text-tertiary">No grants at this level.</p>
        ) : (
          <div className="space-y-1.5">
            {direct.map((g) => (
              <GrantRow
                key={g.id}
                grant={g}
                onRemove={() => remove.mutate(g.id)}
              />
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-end gap-2 pt-1">
          <div className="space-y-1">
            <Label className="text-xs">Principal</Label>
            <Select value={principal} onValueChange={setPrincipal}>
              <SelectTrigger className="w-52">
                <SelectValue placeholder="Member or service account" />
              </SelectTrigger>
              <SelectContent>
                {(data?.principals ?? []).map((p) => (
                  <SelectItem key={p.user_id} value={p.user_id}>
                    {p.name}
                    {p.is_service_account ? " (service)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Tier</Label>
            <Select value={tier} onValueChange={(v) => setTier(v as GrantTier)}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TIERS.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button onClick={addGrant} disabled={upsert.isPending}>
            Grant
          </Button>
        </div>
        {formError && <p className="text-xs text-destructive">{formError}</p>}
      </div>

      {inherited.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">Inherited</p>
          <div className="space-y-1.5">
            {inherited.map((g) => (
              <GrantRow key={g.id} grant={g} inherited />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
