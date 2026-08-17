import { cn } from "@/utils";
import type { ModelStatus, ValidationState } from "@/types/semantic";

const statusStyles: Record<ModelStatus, string> = {
  draft: "bg-[var(--status-queued)] text-white",
  published: "bg-[var(--status-success)] text-white",
  deprecated: "bg-[var(--status-cancelled)] text-white",
};

const statusTitles: Record<ModelStatus, string> = {
  draft: "Not yet authoritative — the assistant will not use it.",
  published: "Authoritative. The assistant answers from these definitions.",
  deprecated: "Retired. Still readable, but excluded from new answers.",
};

/**
 * Publishing state, which here is a trust statement rather than decoration:
 * `published` is precisely the boundary at which a definition starts being
 * quoted back to people as what the organization means.
 */
export function StatusPill({ status }: { status: ModelStatus }) {
  return (
    <span
      title={statusTitles[status]}
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-2xs font-medium",
        statusStyles[status],
      )}
    >
      {status}
    </span>
  );
}

const validationStyles: Record<ValidationState, string> = {
  ok: "bg-[var(--status-success)]/15 text-[var(--status-success)]",
  broken: "bg-[var(--status-failed)]/15 text-[var(--status-failed)]",
  unchecked: "bg-[var(--status-queued)]/15 text-text-secondary",
};

const validationLabels: Record<ValidationState, string> = {
  ok: "resolves",
  broken: "broken",
  unchecked: "unchecked",
};

const validationTitles: Record<ValidationState, string> = {
  ok: "Checked against the catalog, and every column it names is still there.",
  broken:
    "This no longer resolves against the catalog. It is withheld from the assistant until it is fixed.",
  // The distinction that matters: not a softer "ok", but "nobody has looked
  // since something changed".
  unchecked: "Not checked since it last changed. Validate to find out.",
};

export function ValidationPill({ state }: { state: ValidationState }) {
  if (state === "ok") return null;
  return (
    <span
      title={validationTitles[state]}
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-2xs font-medium",
        validationStyles[state],
      )}
    >
      {validationLabels[state]}
    </span>
  );
}
