import { useState } from "react";
import { Play, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/utils";
import {
  useMaintenancePolicy,
  useTriggerScan,
  useUpdatePolicy,
} from "@/queries/maintenance";
import type { Preset, ScanFrequency } from "@/types/maintenance";

const PRESETS: { value: Preset; label: string; blurb: string }[] = [
  {
    value: "conservative",
    label: "Conservative",
    blurb: "Flag only severe issues",
  },
  { value: "balanced", label: "Balanced", blurb: "Recommended default" },
  { value: "aggressive", label: "Aggressive", blurb: "Flag issues early" },
];

const FREQUENCIES: { value: ScanFrequency; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
];

export function MaintenancePage() {
  const { data: policy, isLoading } = useMaintenancePolicy();
  const update = useUpdatePolicy();
  const scan = useTriggerScan();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [syncedPolicy, setSyncedPolicy] = useState<typeof policy>(undefined);

  // Reseed the editable threshold overrides whenever a fresh policy arrives
  // (initial load, or a refetch after saving). Adjusting state during render is
  // React's recommended replacement for a setState-in-effect sync.
  if (policy && policy !== syncedPolicy) {
    setSyncedPolicy(policy);
    setOverrides(
      Object.fromEntries(
        Object.entries(policy.thresholds).map(([k, v]) => [k, String(v)]),
      ),
    );
  }

  if (isLoading || !policy) {
    return (
      <div className="p-4">
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  const save = (patch: Parameters<typeof update.mutate>[0]) =>
    update.mutate(patch, {
      onSuccess: () => toast.success("Maintenance policy updated"),
      onError: (e) => toast.error(String(e)),
    });

  const saveOverrides = () => {
    const thresholds: Record<string, number> = {};
    for (const [k, v] of Object.entries(overrides)) {
      const n = Number(v);
      if (!Number.isNaN(n)) thresholds[k] = n;
    }
    save({ thresholds });
  };

  return (
    <div className="flex h-full flex-col overflow-auto p-4">
      <div className="mx-auto w-full max-w-2xl space-y-4">
        {/* Scanning */}
        <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Autonomous scanning</p>
              <p className="text-xs text-text-secondary">
                Periodically scan tables for health metrics and recommendations.
              </p>
            </div>
            <Button
              variant={policy.scan_enabled ? "default" : "outline"}
              size="sm"
              className="text-xs"
              onClick={() => save({ scan_enabled: !policy.scan_enabled })}
            >
              {policy.scan_enabled ? "Enabled" : "Disabled"}
            </Button>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <span className="text-xs text-text-secondary">Frequency</span>
            <Select
              value={policy.scan_frequency}
              onValueChange={(v) =>
                save({ scan_frequency: v as ScanFrequency })
              }
            >
              <SelectTrigger className="h-8 w-36 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FREQUENCIES.map((f) => (
                  <SelectItem key={f.value} value={f.value} className="text-xs">
                    {f.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {policy.last_scan_at && (
              <span className="text-2xs text-text-tertiary">
                Last scan: {new Date(policy.last_scan_at).toLocaleString()}
              </span>
            )}
          </div>
        </section>

        {/* Preset */}
        <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
          <p className="text-sm font-medium">Maintenance profile</p>
          <p className="text-xs text-text-secondary">
            Sets the thresholds that drive scores and recommendations.
          </p>
          <div className="mt-3 grid grid-cols-3 gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => save({ preset: p.value })}
                className={cn(
                  "rounded-md border p-3 text-left transition-colors",
                  policy.preset === p.value
                    ? "border-[var(--brand-maya-blue)] bg-accent"
                    : "border-[var(--border-subtle)] hover:bg-accent/50",
                )}
                aria-pressed={policy.preset === p.value}
              >
                <p className="text-sm font-medium">{p.label}</p>
                <p className="text-2xs text-text-tertiary">{p.blurb}</p>
              </button>
            ))}
          </div>

          <button
            type="button"
            className="mt-3 text-xs text-text-secondary underline-offset-2 hover:underline"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "Hide" : "Show"} advanced thresholds
          </button>
          {showAdvanced && (
            <div className="mt-3 space-y-2">
              <div className="grid grid-cols-2 gap-2">
                {Object.entries(overrides).map(([k, v]) => (
                  <label key={k} className="text-2xs text-text-secondary">
                    {k}
                    <input
                      className="mt-0.5 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-canvas)] px-2 py-1 font-mono text-xs"
                      value={v}
                      onChange={(e) =>
                        setOverrides((o) => ({ ...o, [k]: e.target.value }))
                      }
                    />
                  </label>
                ))}
              </div>
              <Button
                size="sm"
                variant="outline"
                className="text-xs"
                onClick={saveOverrides}
                disabled={update.isPending}
              >
                Save overrides
              </Button>
            </div>
          )}
        </section>

        {/* Manual scan */}
        <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Run a scan now</p>
              <p className="text-xs text-text-secondary">
                Dispatch a scan cycle immediately, bypassing the schedule.
              </p>
            </div>
            <Button
              size="sm"
              className="gap-1.5 text-xs"
              disabled={scan.isPending}
              onClick={() =>
                scan.mutate(undefined, {
                  onSuccess: (r) =>
                    toast.success(
                      r.status === "ran"
                        ? `Scan dispatched ${r.dispatched} probe(s)`
                        : `Scan ${r.status}`,
                    ),
                  onError: (e) => toast.error(String(e)),
                })
              }
            >
              {scan.isPending ? (
                <RefreshCw className="size-3 animate-spin" />
              ) : (
                <Play className="size-3" />
              )}
              Scan now
            </Button>
          </div>
        </section>
      </div>
    </div>
  );
}
