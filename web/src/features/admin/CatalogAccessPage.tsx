import { useParams } from "@tanstack/react-router";
import { toast } from "sonner";
import { PageToolbar } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCatalogs } from "@/queries/catalogs";
import { useSetAccessMode } from "@/queries/grants";
import type { AccessMode } from "@/types/grant";
import type { Catalog } from "@/types/catalog";

function CatalogAccessRow({ ws, catalog }: { ws: string; catalog: Catalog }) {
  const setMode = useSetAccessMode(ws, catalog.slug);
  return (
    <TableRow>
      <TableCell className="font-medium">{catalog.name}</TableCell>
      <TableCell className="text-text-secondary">{catalog.slug}</TableCell>
      <TableCell className="w-40">
        <Select
          value={catalog.access_mode ?? "open"}
          onValueChange={(v) =>
            setMode.mutate(v as AccessMode, {
              onError: () =>
                toast.error(
                  "Couldn't change access mode — you must be a workspace owner.",
                ),
            })
          }
        >
          <SelectTrigger className="h-8 w-36 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="open">Open</SelectItem>
            <SelectItem value="scoped">Scoped</SelectItem>
          </SelectContent>
        </Select>
      </TableCell>
    </TableRow>
  );
}

export function CatalogAccessPage() {
  const { ws } = useParams({ from: "/$ws/admin" });
  const { data: catalogs, isLoading } = useCatalogs(ws);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageToolbar>
        <p className="text-xs text-text-secondary">
          Choose whether each catalog is <strong>open</strong> (the workspace
          role governs everything) or <strong>scoped</strong> (access is
          narrowed by per-object grants). Grants themselves are managed from the
          catalog view — right-click a catalog, schema, or table → Permissions.
        </p>
      </PageToolbar>

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="p-6">
            <Skeleton className="h-40 w-full" />
          </div>
        ) : (
          <Table
            containerClassName="overflow-visible"
            className="table-gutter text-sm"
          >
            <TableHeader className="sticky top-0 z-10 bg-[var(--bg-surface)]">
              <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
                <TableHead>Catalog</TableHead>
                <TableHead>Slug</TableHead>
                <TableHead>Access mode</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(catalogs ?? []).map((c) => (
                <CatalogAccessRow key={c.id} ws={ws} catalog={c} />
              ))}
              {catalogs?.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={3}
                    className="text-sm text-muted-foreground"
                  >
                    No catalogs attached to this workspace.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
