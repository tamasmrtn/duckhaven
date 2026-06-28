import type { ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { StorageIcon } from "@/components/app/StorageIcon";
import type { BackendKind } from "@/types/storage-backend";
import type { Catalog } from "@/types/catalog";

// Friendly labels for the backend kinds, shared with the tree indicator tooltip.
export const BACKEND_LABELS: Record<string, string> = {
  object_store: "Object storage (MinIO)",
  s3: "AWS S3",
  adls_gen2: "Azure ADLS Gen2",
};

export function backendLabel(kind: string): string {
  return BACKEND_LABELS[kind] ?? kind;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-2 py-1.5">
      <dt className="text-sm text-text-secondary">{label}</dt>
      <dd className="col-span-2 min-w-0 text-sm text-text-primary">
        {children}
      </dd>
    </div>
  );
}

export function CatalogInfoDialog({
  catalog,
  open,
  onOpenChange,
}: {
  catalog: Catalog | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!catalog) return null;
  const kind = catalog.storage_backend_kind;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Catalog information</DialogTitle>
          <DialogDescription>
            Metadata for <span className="font-mono">{catalog.slug}</span>.
          </DialogDescription>
        </DialogHeader>

        <dl className="divide-y divide-[var(--border-subtle)]">
          <Row label="Name">{catalog.name}</Row>
          <Row label="Slug">
            <span className="font-mono text-xs">{catalog.slug}</span>
          </Row>
          <Row label="Polaris name">
            <span className="font-mono text-xs">{catalog.polaris_name}</span>
          </Row>
          <Row label="Storage backend">
            <span className="flex items-center gap-1.5">
              <StorageIcon
                kind={kind as BackendKind}
                className="size-4 shrink-0 text-text-secondary"
              />
              <span>
                {backendLabel(kind)}
                {catalog.storage_backend_name
                  ? ` · ${catalog.storage_backend_name}`
                  : ""}
              </span>
            </span>
          </Row>
          <Row label="Location">
            <span className="font-mono text-xs break-all text-text-secondary">
              {catalog.storage_backend_root_uri || "—"}
            </span>
          </Row>
          <Row label="Default">{catalog.is_default ? "Yes" : "No"}</Row>
          <Row label="Attached workspaces">
            {catalog.attached_workspaces ?? "—"}
          </Row>
          <Row label="Created">
            {new Date(catalog.created_at).toLocaleString()}
          </Row>
        </dl>
      </DialogContent>
    </Dialog>
  );
}
