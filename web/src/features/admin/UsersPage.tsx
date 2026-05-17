import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { ALL_USERS } from "@/mock/fixtures/users";

export function UsersPage() {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary">{ALL_USERS.length} users</p>
      </div>

      <div className="flex-1 overflow-auto p-4">
        <div className="space-y-2">
          {ALL_USERS.map((u) => (
            <div
              key={u.id}
              className="flex items-center gap-3 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3"
            >
              <Avatar className="size-8">
                <AvatarFallback className="text-xs font-medium">
                  {u.name.slice(0, 2).toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">{u.name}</p>
                <p className="text-xs text-text-secondary truncate">
                  {u.email}
                </p>
              </div>
              <Badge
                variant={u.role === "admin" ? "default" : "secondary"}
                className="text-2xs"
              >
                {u.role}
              </Badge>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
