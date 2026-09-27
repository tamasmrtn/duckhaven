import {
  agentAvailability,
  isBetaRuntime,
  type Agent,
  type AgentRequirements,
} from "@/types/agent";

export type AgentResolution =
  | {
      agentId: string;
      source: "worksheet" | "last-used" | "healthy" | "elastic-restart";
      // The agent is stopped; the API restarts it for the run.
      willStart: boolean;
    }
  | { agentId: null; reason: "no-agents" | "none-compatible" };

export interface ResolveAgentInput extends AgentRequirements {
  agents: Agent[];
  // The agent this worksheet last used, when one was picked or run on.
  worksheetAgentId: string | null;
  // The agent this browser last ran on in the workspace.
  lastUsedAgentId: string | null;
}

/**
 * How strongly to prefer an agent nobody picked, lowest first — the order the
 * server uses when it picks: the deployment's default runtime, then other
 * generally available ones, then deprecated ones. An agent on a beta runtime is
 * never a fallback; only a worksheet that already names it runs there.
 */
function runtimeRank(agent: Agent): number | null {
  if (isBetaRuntime(agent)) return null;
  if (agent.runtime?.default) return 0;
  return agent.runtime?.status === "deprecated" ? 2 : 1;
}

function preferred(agents: Agent[]): Agent[] {
  return agents
    .filter((a) => runtimeRank(a) !== null)
    .sort((a, b) => runtimeRank(a)! - runtimeRank(b)!);
}

/**
 * The agent a worksheet should run on.
 *
 * Only an agent a run can actually reach is ever chosen: the worksheet's own,
 * then the last one used here, then the first healthy one, then a degraded one,
 * then a stopped elastic agent the API will start. Never simply the first agent
 * in the list, which is how a worksheet used to land on an offline agent.
 * Beyond the worksheet's own agent, the choice follows the server's runtime
 * preference, so a beta runtime someone is trying out never becomes the default.
 */
export function resolveWorksheetAgent(
  input: ResolveAgentInput,
): AgentResolution {
  const { agents } = input;
  if (agents.length === 0) return { agentId: null, reason: "no-agents" };

  const availability = new Map(
    agents.map((a) => [a.id, agentAvailability(a, input).kind]),
  );
  const usable = (id: string | null): id is string => {
    const kind = id ? availability.get(id) : undefined;
    return kind === "running" || kind === "startable";
  };
  const pick = (
    id: string,
    source: "worksheet" | "last-used" | "healthy" | "elastic-restart",
  ): AgentResolution => ({
    agentId: id,
    source,
    willStart: availability.get(id) === "startable",
  });

  if (usable(input.worksheetAgentId))
    return pick(input.worksheetAgentId, "worksheet");
  const lastUsed = agents.find((a) => a.id === input.lastUsedAgentId);
  if (lastUsed && usable(lastUsed.id) && !isBetaRuntime(lastUsed))
    return pick(lastUsed.id, "last-used");

  const running = preferred(
    agents.filter((a) => availability.get(a.id) === "running"),
  );
  const healthy =
    running.find((a) => a.status === "healthy") ??
    running.find((a) => a.status === "degraded");
  if (healthy) return pick(healthy.id, "healthy");

  const startable = preferred(
    agents.filter((a) => availability.get(a.id) === "startable"),
  )[0];
  if (startable) return pick(startable.id, "elastic-restart");

  return { agentId: null, reason: "none-compatible" };
}

const lastAgentKey = (ws: string) => `dh-last-agent-${ws}`;

export function loadLastUsedAgent(ws: string): string | null {
  try {
    return localStorage.getItem(lastAgentKey(ws));
  } catch {
    return null;
  }
}

export function storeLastUsedAgent(ws: string, agentId: string): void {
  try {
    localStorage.setItem(lastAgentKey(ws), agentId);
  } catch {
    // A per-browser convenience; losing it only costs the default.
  }
}
