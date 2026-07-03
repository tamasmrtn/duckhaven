import { useState } from "react";
import { useParams } from "@tanstack/react-router";
import { ShieldCheck, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCatalogs } from "@/queries/catalogs";
import {
  useCatalogGrants,
  useDeleteGrant,
  useSetAccessMode,
  useUpsertGrant,
} from "@/queries/grants";
import type { GrantTier } from "@/types/grant";

const TIERS: GrantTier[] = ["metadata", "reader", "writer"];

function scopeLabel(schema: string | null, table: string | null): string {
  if (!schema) return "Whole catalog";
  if (!table) return `${schema}.*`;
  return `${schema}.${table}`;
}

export function GrantsPage() {
  const { ws } = useParams({ from: "/$ws/admin" });
  const { data: catalogs, isLoading: catalogsLoading } = useCatalogs(ws);
  // Selected catalog: an explicit choice, else the first one once loaded.
  const [catalogOverride, setCatalogOverride] = useState<string | undefined>();
  const catalog = catalogOverride ?? catalogs?.[0]?.slug;

  const { data, isLoading } = useCatalogGrants(ws, catalog);
  const setMode = useSetAccessMode(ws, catalog ?? "");
  const upsert = useUpsertGrant(ws, catalog ?? "");
  const remove = useDeleteGrant(ws, catalog ?? "");

  const [principal, setPrincipal] = useState<string>("");
  const [schema, setSchema] = useState("");
  const [table, setTable] = useState("");
  const [tier, setTier] = useState<GrantTier>("reader");
  const [error, setError] = useState<string | null>(null);

  const scoped = data?.access_mode === "scoped";

  async function addGrant() {
    setError(null);
    if (!principal || !catalog) {
      setError("Pick a principal first.");
      return;
    }
    try {
      await upsert.mutateAsync({
        user_id: principal,
        schema_name: schema.trim() || null,
        table_name: table.trim() || null,
        tier,
      });
      setSchema("");
      setTable("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save grant.");
    }
  }

  if (catalogsLoading) return <Skeleton className="h-40 w-full" />;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <ShieldCheck className="h-5 w-5" /> Access grants
        </h2>
        <p className="text-sm text-muted-foreground">
          In <strong>scoped</strong> mode, members and service accounts see only
          what they are granted — down to a catalog, schema, or table. A grant
          can only narrow a member's workspace role, never widen it. In{" "}
          <strong>open</strong> mode (the default) the workspace role governs
          everything.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label>Catalog</Label>
          <Select value={catalog} onValueChange={setCatalogOverride}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="Select a catalog" />
            </SelectTrigger>
            <SelectContent>
              {(catalogs ?? []).map((c) => (
                <SelectItem key={c.id} value={c.slug}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label>Access mode</Label>
          <Select
            value={data?.access_mode ?? "open"}
            onValueChange={(v) => setMode.mutate(v as "open" | "scoped")}
            disabled={!catalog || isLoading}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="open">Open</SelectItem>
              <SelectItem value="scoped">Scoped</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {!scoped ? (
        <p className="text-sm text-muted-foreground">
          This catalog is open — grants are not enforced. Switch to{" "}
          <strong>scoped</strong> to manage them.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-3 rounded-md border p-4">
            <div className="space-y-1">
              <Label>Principal</Label>
              <Select value={principal} onValueChange={setPrincipal}>
                <SelectTrigger className="w-56">
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
              <Label>Schema</Label>
              <Input
                className="w-40"
                placeholder="(whole catalog)"
                value={schema}
                onChange={(e) => setSchema(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>Table</Label>
              <Input
                className="w-40"
                placeholder="(whole schema)"
                value={table}
                onChange={(e) => setTable(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>Tier</Label>
              <Select
                value={tier}
                onValueChange={(v) => setTier(v as GrantTier)}
              >
                <SelectTrigger className="w-36">
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
              Add grant
            </Button>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Principal</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data?.grants ?? []).map((g) => (
                <TableRow key={g.id}>
                  <TableCell>{g.user_name ?? g.user_id}</TableCell>
                  <TableCell>
                    {scopeLabel(g.schema_name, g.table_name)}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{g.tier}</Badge>
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Delete grant"
                      onClick={() => remove.mutate(g.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {(data?.grants ?? []).length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-sm text-muted-foreground"
                  >
                    No grants yet — this catalog is invisible to everyone until
                    you add one.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  );
}
