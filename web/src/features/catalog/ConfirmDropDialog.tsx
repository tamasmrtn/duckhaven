import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ApiError } from "@/api/client";

// Destructive confirm: the user must type the object name to proceed.
// For a non-empty schema the API replies 409 asking for cascade; we surface a
// checkbox and require a second confirm.
export function ConfirmDropDialog({
  open,
  onOpenChange,
  kind,
  name,
  onConfirm,
  pending,
  warning,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kind: "table" | "schema";
  name: string;
  onConfirm: (cascade: boolean) => Promise<void>;
  pending: boolean;
  /**
   * Extra consequence to show before the confirm. Used for the blast radius
   * this dialog cannot work out for itself — which published business
   * definitions this drop will break.
   */
  warning?: React.ReactNode;
}) {
  const [typed, setTyped] = useState("");
  const [cascade, setCascade] = useState(false);
  const [needsCascade, setNeedsCascade] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setTyped("");
    setCascade(false);
    setNeedsCascade(false);
    setError(null);
  }

  async function handleConfirm() {
    setError(null);
    try {
      await onConfirm(cascade);
      reset();
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setNeedsCascade(true);
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "Failed to drop");
      }
    }
  }

  const confirmDisabled =
    pending || typed !== name || (needsCascade && !cascade);

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) reset();
        onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            Drop {kind} {name}
          </DialogTitle>
          <DialogDescription>
            Permanently remove this {kind} from the catalog. This cannot be
            undone.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2 text-sm">
          <p className="text-text-secondary">
            This permanently removes <span className="font-mono">{name}</span>{" "}
            from the catalog. This cannot be undone.
          </p>
          {warning}
          {needsCascade && (
            <label className="flex items-center gap-2 text-text-secondary">
              <Checkbox
                checked={cascade}
                onCheckedChange={(v) => setCascade(v === true)}
                aria-label="also drop all tables in this schema"
              />
              Also drop all tables in this schema
            </label>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="confirm-name">
              Type <span className="font-mono">{name}</span> to confirm
            </Label>
            <Input
              id="confirm-name"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoFocus
            />
          </div>
          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={confirmDisabled}
          >
            {pending ? "Dropping…" : `Drop ${kind}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
