import { useState } from "react";
import { Trash2, Users, User as UserIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  useAgentAccess,
  useDeleteAgentGrant,
  useSetAgentAccessMode,
  useUpsertAgentGrant,
} from "@/queries/agents";
import type {
  Agent,
  AgentAccessMode,
  AgentGrant,
  AgentTier,
} from "@/types/agent";

const TIERS: { value: AgentTier; label: string; hint: string }[] = [
  { value: "use", label: "Use", hint: "Run work on it and watch it" },
  {
    value: "operate",
    label: "Operate",
    hint: "Restart, terminate, disconnect",
  },
  { value: "admin", label: "Admin", hint: "Configure, delete, grant access" },
];

// A workspace grant applies to whoever is in that workspace at the time, so
// letting it carry `admin` — which includes granting — would make the ACL
// unauditable. The API rejects it too; this keeps the option from being offered.
const WORKSPACE_TIERS = TIERS.filter((t) => t.value !== "admin");

/** Encodes the picker's selection, since users and workspaces share one list. */
function principalKey(kind: "user" | "workspace", id: string) {
  return `${kind}:${id}`;
}

function GrantRow({
  grant,
  onRemove,
}: {
  grant: AgentGrant;
  onRemove: () => void;
}) {
  const isWorkspace = grant.workspace_id != null;
  const name = isWorkspace
    ? (grant.workspace_name ?? grant.workspace_id)
    : (grant.user_name ?? grant.user_id);
  return (
    <div className="flex items-center gap-2 rounded border border-[var(--border-subtle)] px-3 py-1.5 text-sm">
      {isWorkspace ? (
        <Users className="size-3.5 shrink-0 text-text-tertiary" />
      ) : (
        <UserIcon className="size-3.5 shrink-0 text-text-tertiary" />
      )}
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {isWorkspace && (
        <span className="text-xs text-text-tertiary">every member</span>
      )}
      <Badge variant="secondary">{grant.tier}</Badge>
      <Button
        variant="ghost"
        size="icon"
        className="size-6"
        aria-label={`Remove ${name} grant`}
        onClick={onRemove}
      >
        <Trash2 className="size-3.5" />
      </Button>
    </div>
  );
}

/**
 * Who may use, operate, and administer one agent.
 *
 * Grants are additive and never subtract: someone with a global `agents:manage`
 * keeps full access no matter what is listed here, and on an `open` agent every
 * authenticated user can already use it — a grant there only ever raises someone
 * above that floor.
 */
export function AgentAccessTab({ agent }: { agent: Agent }) {
  const { data, isLoading } = useAgentAccess(agent.id);
  const setMode = useSetAgentAccessMode(agent.id);
  const upsert = useUpsertAgentGrant(agent.id);
  const remove = useDeleteAgentGrant(agent.id);
  const [principal, setPrincipal] = useState("");
  const [tier, setTier] = useState<AgentTier>("use");
  const [formError, setFormError] = useState<string | null>(null);

  if (isLoading) return <Skeleton className="h-40 w-full" />;

  const restricted = data?.access_mode === "restricted";
  const grants = data?.grants ?? [];
  const principals = data?.principals ?? [];
  const [selectedKind] = principal.split(":");
  const tierOptions = selectedKind === "workspace" ? WORKSPACE_TIERS : TIERS;

  async function addGrant() {
    setFormError(null);
    if (!principal) {
      setFormError("Pick a user or workspace first.");
      return;
    }
    const [kind, id] = principal.split(":");
    try {
      await upsert.mutateAsync({
        ...(kind === "workspace" ? { workspace_id: id } : { user_id: id }),
        tier,
      });
      setPrincipal("");
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Could not save grant.");
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-4 rounded-md border border-[var(--border-subtle)] p-3">
        <div>
          <p className="text-sm font-medium">Access mode</p>
          <p className="text-xs text-text-tertiary">
            {restricted
              ? "Restricted — only the users and workspaces granted below can see or use this agent."
              : "Open — anyone signed in can run work on this agent. Grants below still add operate and admin access."}
          </p>
        </div>
        <Select
          value={data?.access_mode ?? "open"}
          onValueChange={(v) => setMode.mutate(v as AgentAccessMode)}
        >
          <SelectTrigger className="w-36" aria-label="Access mode">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="open">Open</SelectItem>
            <SelectItem value="restricted">Restricted</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">Grants</p>
        {grants.length === 0 ? (
          <p className="text-xs text-text-tertiary">
            No grants yet.{" "}
            {restricted
              ? "Only global agent admins can reach this agent."
              : "Everyone can already use this agent."}
          </p>
        ) : (
          <div className="space-y-1.5">
            {grants.map((g) => (
              <GrantRow
                key={g.id}
                grant={g}
                onRemove={() => remove.mutate(g.id)}
              />
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-end gap-2 pt-1">
          <div className="space-y-1">
            <Label className="text-xs">Grant to</Label>
            <Select
              value={principal}
              onValueChange={(v) => {
                setPrincipal(v);
                // A workspace cannot hold `admin`; drop back rather than
                // submitting a tier the API will reject.
                if (v.startsWith("workspace:") && tier === "admin") {
                  setTier("operate");
                }
              }}
            >
              <SelectTrigger className="w-60" aria-label="Grant to">
                <SelectValue placeholder="User or workspace" />
              </SelectTrigger>
              <SelectContent>
                {principals.map((p) => (
                  <SelectItem
                    key={principalKey(p.kind, p.id)}
                    value={principalKey(p.kind, p.id)}
                  >
                    {p.kind === "workspace" ? "Workspace · " : ""}
                    {p.name}
                    {p.is_service_account ? " (service)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Tier</Label>
            <Select value={tier} onValueChange={(v) => setTier(v as AgentTier)}>
              <SelectTrigger className="w-44" aria-label="Tier">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {tierOptions.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    {t.label} — {t.hint}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button onClick={addGrant} disabled={upsert.isPending}>
            Grant
          </Button>
        </div>
        {formError && <p className="text-xs text-destructive">{formError}</p>}
      </div>
    </div>
  );
}
