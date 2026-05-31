import { http, HttpResponse } from "msw";
import { AGENTS } from "../fixtures/agents";

export const agentHandlers = [
  http.get("/api/agents", () => {
    return HttpResponse.json(AGENTS);
  }),

  http.get("/api/admin/agents", () => {
    return HttpResponse.json(AGENTS);
  }),

  http.post("/api/admin/agents/bootstrap", () => {
    const token = `dh_boot_${Math.random().toString(36).slice(2, 18)}`;
    return HttpResponse.json({
      token,
      expires_at: new Date(Date.now() + 86400000).toISOString(),
      control_plane_url: "ws://localhost:8000/agents/connect",
      agent_image: "ghcr.io/tamasmrtn/duckhaven-agent:latest",
    });
  }),

  http.delete("/api/admin/agents/:id/credential", ({ params }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return new HttpResponse(null, { status: 404 });
    agent.status = "unavailable";
    return new HttpResponse(null, { status: 204 });
  }),
];
