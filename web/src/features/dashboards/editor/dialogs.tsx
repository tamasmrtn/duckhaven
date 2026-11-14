import { useState } from "react";
import { Button } from "@/components/ui/button";
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
import type { Dashboard } from "@/types/dashboard";
import { relativeTime } from "@/utils/relativeTime";

/** How a change arrived, completing "saved … from …". */
const VIA: Record<Dashboard["updated_via"], string> = {
  ui: "the app",
  rest: "the REST API",
  cli: "the CLI",
  assistant: "the assistant",
  mcp: "an MCP client",
};

/** "3 minutes ago from the CLI", for whoever saved the dashboard last. */
export function savedWhen(d: Dashboard): string {
  return `${relativeTime(d.updated_at)} from ${VIA[d.updated_via] ?? d.updated_via}`;
}

/** Publish the draft, with a note of what changed. */
export function PublishDialog({
  open,
  onOpenChange,
  dirty,
  onPublish,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Whether the editor holds changes the draft lacks; they are saved first. */
  dirty: boolean;
  onPublish: (message: string | null) => Promise<void>;
}) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await onPublish(message.trim() || null);
      setMessage("");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Publish dashboard</DialogTitle>
          <DialogDescription>
            Everyone who can open the dashboard sees this version, and it is
            kept in its history.{" "}
            {dirty && "Your unsaved changes are saved to the draft first. "}
            Publishing checks every widget against the workspace as you.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5 py-2">
          <Label htmlFor="publish-message" className="text-sm">
            What changed (optional)
          </Label>
          <Input
            id="publish-message"
            value={message}
            maxLength={500}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !busy && void submit()}
            autoFocus
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="brand" onClick={() => void submit()} disabled={busy}>
            {busy ? "Publishing…" : "Publish"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Someone saved the dashboard after this editor loaded it: take their version,
 * or save this one over it.
 */
export function ConflictDialog({
  current,
  base,
  onCancel,
  onLoadTheirs,
  onKeepMine,
}: {
  /** The dashboard as it stands, from the 409; null when there is none. */
  current: Dashboard | null;
  /** The version this editor's changes were made against. */
  base: number;
  onCancel: () => void;
  onLoadTheirs: (current: Dashboard) => void;
  onKeepMine: (current: Dashboard) => void;
}) {
  return (
    <Dialog open={!!current} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>The dashboard changed while you edited it</DialogTitle>
          <DialogDescription>
            {current &&
              `It was saved ${savedWhen(current)}, and is at version ${current.version}. Your changes were made against version ${base}.`}
          </DialogDescription>
        </DialogHeader>
        <ul className="list-disc space-y-1 py-2 pl-5 text-xs text-text-secondary">
          <li>
            <strong className="font-medium text-text-primary">
              Load their version
            </strong>{" "}
            replaces the file in the editor, and your changes are lost.
          </li>
          <li>
            <strong className="font-medium text-text-primary">
              Save mine over it
            </strong>{" "}
            saves your file as the draft, and their changes are lost.
          </li>
        </ul>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            variant="outline"
            onClick={() => current && onLoadTheirs(current)}
          >
            Load their version
          </Button>
          <Button onClick={() => current && onKeepMine(current)}>
            Save mine over it
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Throw the draft away, going back to the published file. */
export function DiscardDialog({
  open,
  onOpenChange,
  onDiscard,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDiscard: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Discard the draft?</DialogTitle>
          <DialogDescription>
            The draft and any unsaved changes are thrown away, for everyone
            editing it. The editor goes back to the published file.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Keep the draft
          </Button>
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void onDiscard().finally(() => setBusy(false));
            }}
          >
            Discard
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Leaving the editor with changes nobody saved. */
export function LeaveDialog({
  open,
  onStay,
  onLeave,
}: {
  open: boolean;
  onStay: () => void;
  onLeave: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onStay()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Leave without saving?</DialogTitle>
          <DialogDescription>
            Your changes to the file are not saved to the draft, and leaving
            loses them.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onStay}>
            Stay
          </Button>
          <Button variant="destructive" onClick={onLeave}>
            Leave
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
