import { useState } from "react";
import { useParams, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Sun, Moon, Monitor } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { cn } from "@/utils";
import { ApiError } from "@/api/client";
import { useMe } from "@/queries/auth";
import { useTheme } from "@/hooks/useTheme";
import { useWorkspace, useWorkspaceMembers } from "@/queries/workspaces";
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
      <div className="flex-1 overflow-auto">
        <Tabs defaultValue="workspace" className="p-6">
          <TabsList>
            <TabsTrigger value="workspace">Workspace</TabsTrigger>
            <TabsTrigger value="account">Account</TabsTrigger>
          </TabsList>
          <TabsContent value="workspace" className="mt-6 max-w-lg">
            <WorkspaceSettings ws={ws} />
          </TabsContent>
          <TabsContent value="account" className="mt-6 max-w-lg">
            <AccountSettings />
          </TabsContent>
        </Tabs>
      </div>
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
