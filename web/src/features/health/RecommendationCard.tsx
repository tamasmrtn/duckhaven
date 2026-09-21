import { Copy, Loader2, Play, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { Recommendation } from "@/types/maintenance";
import { SEVERITY_COLOR, SEVERITY_LABEL, kindLabel } from "./healthStyles";

interface Props {
  rec: Recommendation;
  onDismiss?: (id: string) => void;
  onApply?: (id: string) => void;
  showTable?: boolean;
  dismissing?: boolean;
}

// What the footer says, which depends on whether DuckHaven can run this at all.
// It used to say "does not apply maintenance yet" unconditionally, which is now
// only true for the kinds whose extension has no verbs.
function footer(rec: Recommendation): string {
  const tool = rec.remediation?.tool
    ? `Run with ${rec.remediation.tool}. `
    : "";
  if (rec.apply_status === "running") return "Applying…";
  if (rec.apply_status === "failed") {
    return `Last apply failed: ${rec.apply_error ?? "no detail"}`;
  }
  if (rec.apply_status === "succeeded") {
    return "Applied. The next scan confirms whether the condition cleared.";
  }
  if (rec.remediation?.applicable_in_app) {
    return `${tool}DuckHaven can run this for you.`;
  }
  return `${tool}DuckHaven cannot apply this kind of maintenance.`;
}

function copy(text: string) {
  void navigator.clipboard?.writeText(text);
  toast.success("Copied to clipboard");
}

export function RecommendationCard({
  rec,
  onDismiss,
  onApply,
  showTable = true,
  dismissing = false,
}: Props) {
  const color = SEVERITY_COLOR[rec.severity];
  const applying = rec.apply_status === "running";
  const canApply =
    Boolean(onApply) &&
    Boolean(rec.remediation?.applicable_in_app) &&
    rec.status === "open" &&
    !applying;
  // Two of the five verbs act on the whole catalog, so pressing this from one
  // table's page still affects every table. Say so before, not after.
  const catalogWide = rec.remediation?.scope === "catalog";
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
        <div className="flex shrink-0 items-center gap-1">
          {(canApply || applying) && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-xs"
              disabled={!canApply}
              onClick={() => {
                if (
                  catalogWide &&
                  !window.confirm(
                    `${kindLabel(rec.kind)} acts on every table in this catalog, ` +
                      "not just this one. It rewrites or deletes data files and " +
                      "cannot be undone. Continue?",
                  )
                ) {
                  return;
                }
                onApply?.(rec.id);
              }}
            >
              {applying ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Play className="size-3" />
              )}
              {applying ? "Applying" : "Apply"}
            </Button>
          )}
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
      <p className="mt-2 text-2xs text-text-tertiary">{footer(rec)}</p>
    </div>
  );
}
