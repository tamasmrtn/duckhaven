import { http, HttpResponse } from "msw";
import { AGENTS } from "../fixtures/agents";
import { nextBootstrapToken } from "../lib/seed";
import { httpError } from "../lib/errors";

export const agentHandlers = [
  http.get("/api/agents", () => {
    return HttpResponse.json(AGENTS);
  }),

  http.get("/api/admin/agents", () => {
    return HttpResponse.json(AGENTS);
  }),

  http.post("/api/admin/agents/bootstrap", () => {
    return HttpResponse.json({
      token: nextBootstrapToken(),
      expires_at: new Date(Date.now() + 86400000).toISOString(),
      control_plane_url: "ws://localhost:8000/agents/connect",
      agent_image: "ghcr.io/tamasmrtn/duckhaven-agent:latest",
    });
  }),

  http.delete("/api/admin/agents/:id/credential", ({ params }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    agent.status = "unavailable";
    return new HttpResponse(null, { status: 204 });
  }),
];
