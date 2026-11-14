import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import { dashboardsApi } from "@/api/dashboards";
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

/** A dashboard's name: lower case, starting with a letter, as the server reads it. */
const NAME = /^[a-z][a-z0-9_-]*$/;

/** A name from a title: "Sales overview" is `sales-overview`. */
export function nameFrom(title: string): string {
  const name = title
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return (/^[a-z]/.test(name) || !name ? name : `d-${name}`).slice(0, 64);
}

/** A first file for a new dashboard: one text widget, ready to replace. */
export function starterFile(name: string, title: string): string {
  return [
    "version: 1",
    `name: ${name}`,
    // A JSON string is a double-quoted YAML string, so any title reads back.
    `title: ${JSON.stringify(title)}`,
    "rows:",
    "  - widgets:",
    "      - id: about",
    "        type: text",
    "        markdown: |",
    "          What this dashboard is for, and who reads it.",
    "",
  ].join("\n");
}

/** Start a dashboard as a draft, and open it in the editor. */
export function NewDashboardDialog({
  ws,
  open,
  onOpenChange,
}: {
  ws: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  // Follows the title until someone types a name of their own.
  const [typed, setTyped] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = typed ?? nameFrom(title);
  const valid = title.trim() !== "" && NAME.test(name);

  async function create() {
    if (!valid || busy) return;
    setBusy(true);
    try {
      const created = await dashboardsApi.create(
        ws,
        starterFile(name, title.trim()),
      );
      void queryClient.invalidateQueries({
        queryKey: ["workspace", ws, "dashboards"],
      });
      onOpenChange(false);
      void navigate({
        to: "/$ws/dashboards/$dashboard/edit",
        params: { ws, dashboard: created.name },
      });
    } catch (err) {
      setProblem(
        err instanceof ApiError || err instanceof Error
          ? err.message
          : "The dashboard could not be created.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New dashboard</DialogTitle>
          <DialogDescription>
            It starts as a draft only writers can see, with one text widget, and
            opens in the editor.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="dashboard-title" className="text-sm">
              Title
            </Label>
            <Input
              id="dashboard-title"
              value={title}
              maxLength={120}
              onChange={(e) => {
                setTitle(e.target.value);
                setProblem(null);
              }}
              onKeyDown={(e) => e.key === "Enter" && void create()}
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="dashboard-name" className="text-sm">
              Name
            </Label>
            <Input
              id="dashboard-name"
              value={name}
              maxLength={64}
              className="font-mono"
              onChange={(e) => {
                setTyped(e.target.value);
                setProblem(null);
              }}
              onKeyDown={(e) => e.key === "Enter" && void create()}
              aria-describedby="dashboard-name-hint"
            />
            <p id="dashboard-name-hint" className="text-2xs text-text-tertiary">
              Its address and its file&apos;s name: lower-case letters, digits,
              hyphens and underscores, starting with a letter.
            </p>
          </div>
          {problem && (
            <p
              role="alert"
              className="rounded border border-[var(--status-failed)]/40 bg-[var(--status-failed)]/10 px-3 py-2 text-xs text-text-primary"
            >
              {problem}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="brand"
            onClick={() => void create()}
            disabled={!valid || busy}
          >
            {busy ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
