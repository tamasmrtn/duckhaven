import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { PendingApproval } from "@/types/assistant";

/** Confirms a write the assistant proposed before it runs. */
export function WriteApprovalDialog({
  pending,
  onResolve,
}: {
  pending: PendingApproval | null;
  onResolve: (approved: boolean) => void;
}) {
  return (
    <Dialog
      open={pending != null}
      onOpenChange={(open) => !open && onResolve(false)}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Approve write?</DialogTitle>
          <DialogDescription>
            The assistant wants to run a statement that changes data. It runs
            only if you approve, and is still subject to your permissions.
          </DialogDescription>
        </DialogHeader>
        {pending?.sql && (
          <pre className="max-h-48 overflow-auto rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3 font-mono text-xs">
            {pending.sql}
          </pre>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onResolve(false)}>
            Deny
          </Button>
          <Button onClick={() => onResolve(true)}>Approve &amp; run</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
