import { Database, Box, Cloud } from "lucide-react";
import type { BackendKind } from "@/types/storage-backend";
import { cn } from "@/utils";

interface StorageIconProps {
  // Null/undefined when a workspace has no catalog yet (no default storage).
  kind: BackendKind | null | undefined;
  className?: string;
}

const icons: Record<
  BackendKind,
  React.ComponentType<{ className?: string }>
> = {
  object_store: Database,
  s3: Box,
  adls_gen2: Cloud,
};

const labels: Record<BackendKind, string> = {
  object_store: "Object storage",
  s3: "S3",
  adls_gen2: "ADLS",
};

export function StorageIcon({ kind, className }: StorageIconProps) {
  const Icon = kind ? icons[kind] : Database;
  const label = kind ? labels[kind] : "No storage";
  return <Icon className={cn("size-4", className)} aria-label={label} />;
}

export function StorageLabel({
  kind,
}: {
  kind: BackendKind | null | undefined;
}) {
  return <span className="font-mono text-xs">{kind ? labels[kind] : "—"}</span>;
}
