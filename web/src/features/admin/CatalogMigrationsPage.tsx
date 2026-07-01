import { useState } from "react";
import { ArrowRight, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAllCatalogs } from "@/queries/catalogs";
import { useStorageBackends } from "@/queries/storage-backends";
import {
  useCancelMigration,
  useCatalogMigration,
  useCatalogMigrations,
  useStartMigration,
} from "@/queries/catalog-migrations";
import { MigrationLogViewer } from "./MigrationLogViewer";
import {
  MIGRATION_TERMINAL,
  type MigrationStatus,
} from "@/types/catalog-migration";
import type { Catalog } from "@/types/catalog";

const BADGE_VARIANT: Record<
  MigrationStatus,
  "default" | "secondary" | "destructive"
> = {
  completed: "default",
  failed: "destructive",
  cancelled: "secondary",
  pending: "secondary",
  copying: "secondary",
  verifying: "secondary",
  cutover: "secondary",
};

function isActive(status: MigrationStatus): boolean {
  return !MIGRATION_TERMINAL.includes(status);
}

export function CatalogMigrationsPage() {
  const { data: catalogs, isLoading } = useAllCatalogs();
  const [dialogCatalog, setDialogCatalog] = useState<Catalog | null>(null);
  const [selectedCatalogId, setSelectedCatalogId] = useState<string | null>(
    null,
  );

  return (
    <div className="h-full overflow-auto p-6">
      <h2 className="text-md font-semibold">Storage migrations</h2>
      <p className="mt-1 text-sm text-text-secondary">
        Move a catalog's data to a different storage backend. The catalog stays
        read-only (writes are rejected) until the migration finishes and cuts
        over automatically.
      </p>

      <div className="mt-4 overflow-hidden rounded-md border border-[var(--border-subtle)]">
        <table className="w-full text-sm">
          <thead className="bg-[var(--bg-surface)] text-left text-text-secondary">
            <tr>
              <th className="px-4 py-2 font-medium">Catalog</th>
              <th className="px-4 py-2 font-medium">Current backend</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td className="px-4 py-3" colSpan={3}>
                  <Skeleton className="h-5 w-40" />
                </td>
              </tr>
            )}
            {catalogs?.map((c) => (
              <tr key={c.id} className="border-t border-[var(--border-subtle)]">
                <td className="px-4 py-2 font-medium">{c.slug}</td>
                <td className="px-4 py-2 text-text-secondary">
                  {c.storage_backend_name ?? c.storage_backend_kind}
                </td>
                <td className="px-4 py-2 text-right">
                  <div className="flex justify-end gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setSelectedCatalogId(c.id)}
                    >
                      Migrations
                    </Button>
                    <Button size="sm" onClick={() => setDialogCatalog(c)}>
                      Migrate…
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedCatalogId && (
        <MigrationPanel
          catalogId={selectedCatalogId}
          catalogSlug={
            catalogs?.find((c) => c.id === selectedCatalogId)?.slug ?? ""
          }
        />
      )}

      <MigrateDialog
        catalog={dialogCatalog}
        onClose={() => setDialogCatalog(null)}
        onStarted={(catalogId) => setSelectedCatalogId(catalogId)}
      />
    </div>
  );
}

function MigrateDialog({
  catalog,
  onClose,
  onStarted,
}: {
  catalog: Catalog | null;
  onClose: () => void;
  onStarted: (catalogId: string) => void;
}) {
  const { data: backends = [] } = useStorageBackends();
  const start = useStartMigration();
  const [target, setTarget] = useState<string | undefined>(undefined);

  // The current backend is not a valid target.
  const choices = backends.filter((b) => b.id !== catalog?.storage_backend_id);

  async function handleStart() {
    if (!catalog || !target) return;
    try {
      await start.mutateAsync({
        catalogId: catalog.id,
        targetStorageBackendId: target,
      });
      toast.success(`Migration started for ${catalog.slug}`);
      onStarted(catalog.id);
      setTarget(undefined);
      onClose();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to start migration",
      );
    }
  }

  return (
    <Dialog
      open={catalog !== null}
      onOpenChange={(v) => {
        if (!v) {
          setTarget(undefined);
          onClose();
        }
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Migrate storage backend</DialogTitle>
          <DialogDescription>
            Move catalog <span className="font-medium">{catalog?.slug}</span> to
            a new storage backend. Writes will be rejected until the migration
            completes.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5 py-2">
          <Label htmlFor="target-backend">Target backend</Label>
          <Select value={target ?? ""} onValueChange={setTarget}>
            <SelectTrigger id="target-backend" aria-label="target backend">
              <SelectValue placeholder="Select a backend…" />
            </SelectTrigger>
            <SelectContent>
              {choices.map((b) => (
                <SelectItem key={b.id} value={b.id}>
                  {b.name} ({b.kind})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleStart} disabled={!target || start.isPending}>
            {start.isPending ? "Starting…" : "Start migration"}
            <ArrowRight className="size-3" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MigrationPanel({
  catalogId,
  catalogSlug,
}: {
  catalogId: string;
  catalogSlug: string;
}) {
  const { data: migrations = [], isLoading } = useCatalogMigrations(catalogId);
  const [picked, setPicked] = useState<string | null>(null);
  // Default to the newest migration; the user can pick an older one.
  const selectedId = picked ?? migrations[0]?.id ?? null;

  if (isLoading) return <Skeleton className="mt-6 h-24 w-full" />;
  if (migrations.length === 0)
    return (
      <p className="mt-6 text-sm text-text-secondary">
        No migrations for {catalogSlug} yet.
      </p>
    );

  return (
    <div className="mt-6">
      <h3 className="text-sm font-semibold">Migrations for {catalogSlug}</h3>
      <div className="mt-2 flex flex-wrap gap-2">
        {migrations.map((m) => (
          <button
            key={m.id}
            type="button"
            onClick={() => setPicked(m.id)}
            className={
              "rounded-md border px-2 py-1 text-xs " +
              (m.id === selectedId
                ? "border-[var(--border-strong)] bg-accent"
                : "border-[var(--border-subtle)]")
            }
          >
            <Badge variant={BADGE_VARIANT[m.status]}>{m.status}</Badge>
            <span className="ml-2 text-text-tertiary">
              {new Date(m.created_at).toLocaleString()}
            </span>
          </button>
        ))}
      </div>
      {selectedId && (
        <MigrationDetail catalogId={catalogId} migrationId={selectedId} />
      )}
    </div>
  );
}

function MigrationDetail({
  catalogId,
  migrationId,
}: {
  catalogId: string;
  migrationId: string;
}) {
  const { data: migration } = useCatalogMigration(catalogId, migrationId);
  const cancel = useCancelMigration();
  if (!migration) return null;

  const active = isActive(migration.status);
  const pct =
    migration.tables_total > 0
      ? Math.round((migration.tables_done / migration.tables_total) * 100)
      : 0;
  // Cancellation is only possible before the atomic cutover.
  const cancellable = active && migration.status !== "cutover";

  return (
    <div className="mt-3 space-y-3">
      <div className="flex items-center gap-3">
        <Badge variant={BADGE_VARIANT[migration.status]}>
          {active && <RefreshCw className="mr-1 size-3 animate-spin" />}
          {migration.status}
        </Badge>
        <span className="text-sm text-text-secondary">
          {migration.tables_done}/{migration.tables_total} tables
        </span>
        {cancellable && (
          <Button
            size="sm"
            variant="outline"
            disabled={cancel.isPending}
            onClick={() =>
              cancel.mutate(
                { catalogId, id: migration.id },
                {
                  onSuccess: () => toast.success("Cancellation requested"),
                  onError: (e) => toast.error(String(e)),
                },
              )
            }
          >
            Cancel
          </Button>
        )}
      </div>

      <div
        className="h-2 overflow-hidden rounded-full bg-[var(--bg-surface)]"
        role="progressbar"
        aria-valuenow={pct}
      >
        <div
          className="h-full bg-[var(--brand-slate-blue)] transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      {migration.error && (
        <p className="text-xs text-red-500">{migration.error}</p>
      )}

      <MigrationLogViewer
        catalogId={catalogId}
        migrationId={migration.id}
        active={active}
      />
    </div>
  );
}
