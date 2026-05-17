import { HardDrive, Server, Box, Cloud } from "lucide-react";
import type { BackendKind } from "@/types/storage-backend";
import { cn } from "@/utils";

interface StorageIconProps {
  kind: BackendKind;
  className?: string;
}

const icons: Record<
  BackendKind,
  React.ComponentType<{ className?: string }>
> = {
  local_fs: HardDrive,
  nas: Server,
  s3: Box,
  adls_gen2: Cloud,
};

const labels: Record<BackendKind, string> = {
  local_fs: "Local FS",
  nas: "NAS",
  s3: "S3",
  adls_gen2: "ADLS",
};

export function StorageIcon({ kind, className }: StorageIconProps) {
  const Icon = icons[kind];
  return <Icon className={cn("size-4", className)} aria-label={labels[kind]} />;
}

export function StorageLabel({ kind }: { kind: BackendKind }) {
  return <span className="font-mono text-xs">{labels[kind]}</span>;
}
