import { Copy, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { Recommendation } from "@/types/maintenance";
import { SEVERITY_COLOR, SEVERITY_LABEL, kindLabel } from "./healthStyles";

interface Props {
  rec: Recommendation;
  onDismiss?: (id: string) => void;
  showTable?: boolean;
  dismissing?: boolean;
}

function copy(text: string) {
  void navigator.clipboard?.writeText(text);
  toast.success("Copied to clipboard");
}

export function RecommendationCard({
  rec,
  onDismiss,
  showTable = true,
  dismissing = false,
}: Props) {
  const color = SEVERITY_COLOR[rec.severity];
  return (
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className="rounded-full px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide"
              style={{ color, border: `1px solid ${color}` }}
            >
              {SEVERITY_LABEL[rec.severity]}
            </span>
            <span className="text-sm font-medium text-text-primary">
              {kindLabel(rec.kind)}
            </span>
            <span className="text-2xs uppercase tracking-wide text-text-tertiary">
              {rec.confidence} confidence
            </span>
          </div>
          {showTable && (
            <p className="mt-1 font-mono text-xs text-text-secondary">
              {rec.schema_name}.{rec.table_name}
            </p>
          )}
        </div>
        {onDismiss && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs"
            disabled={dismissing}
            onClick={() => onDismiss(rec.id)}
          >
            <X className="size-3" />
            Dismiss
          </Button>
        )}
      </div>

      <p className="mt-2 text-sm text-text-secondary">{rec.rationale}</p>

      {rec.remediation?.command && (
        <div className="mt-3 flex items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] p-2">
          <code className="min-w-0 flex-1 truncate font-mono text-xs text-text-secondary">
            {rec.remediation.command}
          </code>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 shrink-0 gap-1 text-2xs"
            onClick={() => copy(rec.remediation!.command!)}
          >
            <Copy className="size-3" />
            Copy
          </Button>
        </div>
      )}
      <p className="mt-2 text-2xs text-text-tertiary">
        {rec.remediation?.tool ? `Run with ${rec.remediation.tool}. ` : ""}
        DuckHaven recommends but does not apply maintenance yet.
      </p>
    </div>
  );
}
