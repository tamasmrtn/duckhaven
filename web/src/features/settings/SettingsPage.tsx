import { useState } from "react";
import { useParams, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Sun, Moon, Monitor } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader, PageToolbar } from "@/components/ui/page-header";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/utils";
import { ApiError } from "@/api/client";
import { useMe } from "@/queries/auth";
import { useTheme } from "@/hooks/useTheme";
import { useAgents } from "@/queries/agents";
import { useCatalogs } from "@/queries/catalogs";
import { useWorkspace, useWorkspaceMembers } from "@/queries/workspaces";
import { agentAvailability } from "@/types/agent";
import {
  useUpdateWorkspace,
  useDeleteWorkspace,
} from "@/queries/workspaces.mutations";
import { ConfirmDropDialog } from "@/features/catalog/ConfirmDropDialog";

export function SettingsPage() {
  const { ws } = useParams({ from: "/$ws/settings" });

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Settings" />
      <Tabs defaultValue="workspace" className="flex min-h-0 flex-1 flex-col">
        <PageToolbar>
          <TabsList>
            <TabsTrigger value="workspace">Workspace</TabsTrigger>
            <TabsTrigger value="account">Account</TabsTrigger>
          </TabsList>
        </PageToolbar>
        <div className="flex-1 overflow-auto p-6">
          <TabsContent value="workspace" className="mt-0 max-w-lg">
            <WorkspaceSettings ws={ws} />
          </TabsContent>
          <TabsContent value="account" className="mt-0 max-w-lg">
            <AccountSettings />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}

function WorkspaceSettings({ ws }: { ws: string }) {
  const navigate = useNavigate();
  const { data: workspace } = useWorkspace(ws);
  const { data: me } = useMe();
  const { data: members = [] } = useWorkspaceMembers(ws);
  const myRole = members.find((m) => m.user_id === me?.id)?.role;
  const isOwner = myRole === "owner";
  const resultCacheEnabled = workspace?.result_cache_enabled ?? true;

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // Sync local edit state from the loaded workspace exactly once per
  // workspace, per React's documented "adjusting state when a prop changes"
  // pattern — not a useEffect, so it doesn't fight the user's in-progress edits
  // on refetch.
  const [syncedFor, setSyncedFor] = useState<string | undefined>();
  if (workspace && workspace.id !== syncedFor) {
    setSyncedFor(workspace.id);
    setName(workspace.name);
    setDescription(workspace.description ?? "");
  }

  const update = useUpdateWorkspace(ws);
  const del = useDeleteWorkspace(ws);
  const [deleteOpen, setDeleteOpen] = useState(false);

  async function handleSave() {
    try {
      await update.mutateAsync({ name, description });
      toast.success("Workspace updated");
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.message
          : "Failed to update the workspace",
      );
    }
  }

  if (!workspace) return null;

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="ws-name">Name</Label>
          <Input
            id="ws-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!isOwner}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ws-description">Description</Label>
          <textarea
            id="ws-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={!isOwner}
            rows={3}
            className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>
        {!isOwner && (
          <p className="text-xs text-text-tertiary">
            Only a workspace owner can rename or describe this workspace.
          </p>
        )}
        <Button
          onClick={handleSave}
          disabled={!isOwner || update.isPending || name.trim() === ""}
        >
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </div>

      <div className="space-y-2 border-t border-[var(--border-subtle)] pt-6">
        <p className="text-sm font-medium text-text-primary">Result cache</p>
        <div className="flex items-start gap-2">
          <Checkbox
            id="ws-result-cache"
            checked={resultCacheEnabled}
            disabled={!isOwner || update.isPending}
            onCheckedChange={async (checked) => {
              try {
                await update.mutateAsync({
                  result_cache_enabled: checked === true,
                });
                toast.success(
                  checked === true
                    ? "Result cache turned on"
                    : "Result cache turned off",
                );
              } catch (err) {
                toast.error(
                  err instanceof ApiError
                    ? err.message
                    : "Failed to update the result cache",
                );
              }
            }}
          />
          <div className="space-y-1">
            <Label htmlFor="ws-result-cache">
              Answer repeated queries from the result cache
            </Label>
            <p className="text-xs text-text-tertiary">
              A read is served from an earlier run's result when nothing it
              reads has changed since.{" "}
              {resultCacheEnabled
                ? "Turn this off to run every query."
                : "Turn this on to answer repeated queries from earlier results."}
            </p>
          </div>
        </div>
      </div>

      <DefaultCompute ws={ws} isOwner={isOwner} />

      <div className="space-y-2 border-t border-[var(--border-subtle)] pt-6">
        <p className="text-sm font-medium text-text-primary">Danger zone</p>
        <p className="text-sm text-text-tertiary">
          Deleting a workspace removes its queries, saved queries, schedules,
          and assistant history. Attached catalogs are not affected.
        </p>
        <Button
          variant="destructive"
          disabled={!isOwner}
          onClick={() => setDeleteOpen(true)}
        >
          Delete workspace…
        </Button>
      </div>

      <ConfirmDropDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        kind="workspace"
        name={workspace.name}
        pending={del.isPending}
        onConfirm={async () => {
          await del.mutateAsync();
          toast.success(`Deleted ${workspace.name}`);
          void navigate({ to: "/welcome" });
        }}
      />
    </div>
  );
}

const NO_DEFAULT = "";

function availabilityHint(kind: string): string {
  if (kind === "startable") return " — stopped, starts on use";
  if (kind === "running") return "";
  return " — unavailable";
}

function DefaultCompute({ ws, isOwner }: { ws: string; isOwner: boolean }) {
  const { data: workspace } = useWorkspace(ws);
  const { data: agents = [] } = useAgents();
  const { data: catalogs = [] } = useCatalogs(ws);
  const update = useUpdateWorkspace(ws);
  if (!workspace) return null;

  const needs = {
    catalogKinds: catalogs.map((c) => c.kind),
    backends: workspace.storage_backend_kind
      ? [workspace.storage_backend_kind]
      : [],
  };
  // What the owner may use. An agent that cannot serve this workspace is left
  // out; the server refuses it too, and the list is only worth choosing from if
  // every entry can work.
  const options = agents
    .map((agent) => ({ agent, kind: agentAvailability(agent, needs).kind }))
    .filter(({ kind }) => kind !== "incompatible");
  const current = workspace.default_agent_id ?? NO_DEFAULT;
  // The current default may be one this owner has no grant on (someone else set
  // it); keep it selectable so the control shows the truth.
  const currentMissing =
    current !== NO_DEFAULT &&
    !options.some(({ agent }) => agent.id === current);

  async function change(value: string) {
    try {
      await update.mutateAsync({
        default_agent_id: value === NO_DEFAULT ? null : value,
      });
      toast.success(
        value === NO_DEFAULT
          ? "Default compute cleared"
          : "Default compute updated",
      );
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.message
          : "Failed to update the default compute",
      );
    }
  }

  return (
    <div className="space-y-2 border-t border-[var(--border-subtle)] pt-6">
      <Label htmlFor="ws-default-compute" className="text-sm font-medium">
        Default compute
      </Label>
      <select
        id="ws-default-compute"
        value={current}
        disabled={!isOwner || update.isPending}
        onChange={(e) => void change(e.target.value)}
        className="h-7 w-full rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-sm text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
      >
        <option value={NO_DEFAULT}>None — pick automatically</option>
        {currentMissing && workspace.default_agent && (
          <option value={workspace.default_agent.id}>
            {workspace.default_agent.name}
          </option>
        )}
        {options.map(({ agent, kind }) => (
          <option key={agent.id} value={agent.id}>
            {agent.name}
            {availabilityHint(kind)}
            {agent.access_mode === "restricted" ? " (restricted)" : ""}
          </option>
        ))}
      </select>
      <p className="text-xs text-text-tertiary">
        The assistant, MCP, SQL sessions and any query that names no agent run
        here. Every member of the workspace can use it, whether or not they have
        access to the agent. If it is stopped, running anything starts it; if it
        cannot run the work, the request fails instead of using another agent.
      </p>
      {isOwner && (
        <p className="text-xs text-text-tertiary">
          Making a restricted agent the default needs the operate tier on it.
        </p>
      )}
      {!isOwner && (
        <p className="text-xs text-text-tertiary">
          Only a workspace owner can change the default compute.
        </p>
      )}
    </div>
  );
}

function AccountSettings() {
  const { data: me } = useMe();
  const { theme, setTheme } = useTheme();

  const options: {
    value: "light" | "dark" | "system";
    label: string;
    icon: typeof Sun;
  }[] = [
    { value: "light", label: "Light", icon: Sun },
    { value: "dark", label: "Dark", icon: Moon },
    { value: "system", label: "System", icon: Monitor },
  ];

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <p className="text-sm font-medium text-text-primary">Profile</p>
        <div className="space-y-1.5">
          <Label>Name</Label>
          <p className="text-sm text-text-secondary">{me?.name}</p>
        </div>
        <div className="space-y-1.5">
          <Label>Email</Label>
          <p className="text-sm text-text-secondary">{me?.email}</p>
        </div>
        <div className="space-y-1.5">
          <Label>Sign-in method</Label>
          <p className="text-sm text-text-secondary">{me?.auth_provider}</p>
        </div>
      </div>

      <div className="space-y-2 border-t border-[var(--border-subtle)] pt-6">
        <p className="text-sm font-medium text-text-primary">Theme</p>
        <div className="flex gap-2" role="radiogroup" aria-label="Theme">
          {options.map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={theme === value}
              onClick={() => setTheme(value)}
              className={cn(
                "flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm",
                theme === value
                  ? "border-[var(--brand-slate-blue)] bg-accent text-text-primary"
                  : "border-[var(--border-subtle)] text-text-secondary hover:bg-accent/50",
              )}
            >
              <Icon className="size-4" />
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
