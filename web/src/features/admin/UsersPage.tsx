import { useState } from "react";
import { MoreHorizontal, UserPlus, Users } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAdminUsers,
  useCreateUser,
  useRemoveUserWorkspace,
  useRevokeSessions,
  useSetUserWorkspaceRole,
  useUpdateUser,
  useUserWorkspaces,
} from "@/queries/users";
import type { User } from "@/types/auth";

const ROLES = ["admin", "user"];
const WORKSPACE_ROLES = ["reader", "writer", "owner"];

function CreateUserDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateUser();

  async function handleCreate() {
    setError(null);
    try {
      await create.mutateAsync({ email, name, password, role });
      setEmail("");
      setName("");
      setPassword("");
      setRole("user");
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create user.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add user</DialogTitle>
          <DialogDescription>
            Create a local account. Federated (SSO/LDAP) users are provisioned
            automatically on first sign-in.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="new-user-email">Email</Label>
            <Input
              id="new-user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-user-name">Name</Label>
            <Input
              id="new-user-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-user-password">Temporary password</Label>
            <Input
              id="new-user-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Role</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {error && (
            <p className="text-xs text-[var(--status-failed)]" role="alert">
              {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button
            onClick={handleCreate}
            disabled={create.isPending || !email || !name || !password}
          >
            {create.isPending ? "Creating…" : "Create user"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ManageWorkspacesDialog({
  userId,
  userName,
  open,
  onOpenChange,
}: {
  userId: string;
  userName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: workspaces = [], isLoading } = useUserWorkspaces(userId, open);
  const setRole = useSetUserWorkspaceRole(userId);
  const remove = useRemoveUserWorkspace(userId);

  function change(slug: string, value: string) {
    if (value === "none") remove.mutate(slug);
    else setRole.mutate({ ws: slug, role: value });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Workspace access</DialogTitle>
          <DialogDescription>
            Grant {userName} a role in each workspace, or remove access.
            Workspace roles are separate from the global role.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-80 space-y-2 overflow-auto">
          {isLoading ? (
            <Skeleton className="h-12 w-full animate-shimmer rounded-md" />
          ) : workspaces.length === 0 ? (
            <p className="py-6 text-center text-sm text-text-secondary">
              No workspaces yet.
            </p>
          ) : (
            workspaces.map((w) => (
              <div
                key={w.workspace_id}
                className="flex items-center justify-between gap-3 rounded-md border border-[var(--border-subtle)] p-2"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{w.name}</p>
                  <p className="truncate text-xs text-text-secondary">
                    {w.slug}
                  </p>
                </div>
                <Select
                  value={w.role ?? "none"}
                  onValueChange={(v) => change(w.slug, v)}
                >
                  <SelectTrigger
                    className="h-8 w-32"
                    aria-label={`Role in ${w.name}`}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">No access</SelectItem>
                    {WORKSPACE_ROLES.map((r) => (
                      <SelectItem key={r} value={r}>
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function UserRow({ user }: { user: User }) {
  const update = useUpdateUser();
  const revoke = useRevokeSessions();
  const [workspacesOpen, setWorkspacesOpen] = useState(false);

  return (
    <div className="flex items-center gap-3 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3">
      <Avatar className="size-8">
        <AvatarFallback className="text-xs font-medium">
          {user.name.slice(0, 2).toUpperCase()}
        </AvatarFallback>
      </Avatar>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">{user.name}</p>
        <p className="text-xs text-text-secondary truncate">{user.email}</p>
      </div>
      {user.auth_provider !== "local" && (
        <Badge variant="outline" className="text-2xs uppercase">
          {user.auth_provider}
        </Badge>
      )}
      {!user.is_active && (
        <Badge variant="secondary" className="text-2xs">
          Disabled
        </Badge>
      )}
      <Select
        value={user.role}
        onValueChange={(role) =>
          update.mutate({ id: user.id, input: { role } })
        }
      >
        <SelectTrigger
          className="h-8 w-28"
          aria-label={`Role for ${user.name}`}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {ROLES.map((r) => (
            <SelectItem key={r} value={r}>
              {r}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label={`Actions for ${user.name}`}
          >
            <MoreHorizontal className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => setWorkspacesOpen(true)}>
            Manage workspaces
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() =>
              update.mutate({
                id: user.id,
                input: { is_active: !user.is_active },
              })
            }
          >
            {user.is_active ? "Deactivate" : "Activate"}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => revoke.mutate(user.id)}>
            Revoke sessions
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ManageWorkspacesDialog
        userId={user.id}
        userName={user.name}
        open={workspacesOpen}
        onOpenChange={setWorkspacesOpen}
      />
    </div>
  );
}

export function UsersPage() {
  const { data: users = [], isLoading } = useAdminUsers();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary">{users.length} users</p>
        <Button size="sm" className="h-8" onClick={() => setCreateOpen(true)}>
          <UserPlus className="size-4" />
          Add user
        </Button>
      </div>

      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} />

      <div className="flex-1 overflow-auto p-4">
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-14 w-full animate-shimmer rounded-md"
              />
            ))}
          </div>
        ) : users.length === 0 ? (
          <EmptyState icon={Users} title="No users yet." />
        ) : (
          <div className="space-y-2">
            {users.map((u) => (
              <UserRow key={u.id} user={u} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
