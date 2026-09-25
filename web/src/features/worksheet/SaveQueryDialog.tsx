import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
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
import { useSaveQuery } from "@/queries/queries";
import type { Agent } from "@/types/agent";
import type { SavedQuery } from "@/types/saved-query";

const NO_AGENT = "";

interface SaveQueryDialogProps {
  ws: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sql: string;
  initialName: string;
  // The agents a scheduled run could fall back to, and the one to preselect.
  agents: Agent[];
  initialAgentId: string | null;
  onSaved: (saved: SavedQuery) => void;
}

/**
 * Save a worksheet as a new shared query ("Save as").
 *
 * A name already in use (ignoring case) is not silently overwritten: the
 * server answers 409 and the dialog asks before replacing a query others may
 * rely on.
 */
export function SaveQueryDialog({
  ws,
  open,
  onOpenChange,
  sql,
  initialName,
  agents,
  initialAgentId,
  onSaved,
}: SaveQueryDialogProps) {
  const [name, setName] = useState(initialName);
  const [agentId, setAgentId] = useState(initialAgentId ?? NO_AGENT);
  // Set once the server says the name is taken; the next submit replaces it.
  const [taken, setTaken] = useState<string | null>(null);
  const save = useSaveQuery(ws);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      const saved = await save.mutateAsync({
        name: trimmed,
        sql,
        default_agent_id: agentId || null,
        onConflict: taken ? "replace" : "error",
      });
      onSaved(saved);
    } catch (err) {
      if (err instanceof ApiError && err.code === "saved_query_exists") {
        setTaken(String(err.details?.name ?? trimmed));
        return;
      }
      // Leave the dialog open so the user can retry after a failed save.
      toast.error(err instanceof Error ? err.message : "Couldn't save query.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Save query</DialogTitle>
          <DialogDescription>
            Save this worksheet as a named query everyone in the workspace can
            open and schedule. The worksheet stays linked, so Save updates it.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="save-name" className="text-sm">
              Name
            </Label>
            <Input
              id="save-name"
              placeholder="My query name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setTaken(null);
              }}
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && void submit()}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="save-agent" className="text-sm">
              Default agent
            </Label>
            <select
              id="save-agent"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              className="h-8 w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-sm text-text-primary"
            >
              <option value={NO_AGENT}>None — schedules pick one</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
            <p className="text-2xs text-text-tertiary">
              A schedule without its own agent runs on this one.
            </p>
          </div>
          {taken && (
            <p
              role="alert"
              className="rounded border border-[var(--status-running)]/40 bg-[var(--status-running)]/10 px-3 py-2 text-xs text-text-primary"
            >
              A saved query named “{taken}” already exists in this workspace.
              Replacing it changes it for everyone who uses it, and for its
              schedules.
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={!name.trim() || save.isPending}
            variant={taken ? "destructive" : "default"}
          >
            {taken ? "Replace" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
