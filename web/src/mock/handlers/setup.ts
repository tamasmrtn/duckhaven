import { http, HttpResponse } from "msw";
import { CURRENT_USER } from "../fixtures/users";

export const setupHandlers = [
  http.get("/api/setup/status", () => {
    return HttpResponse.json({ needs_admin: false });
  }),

  http.post("/api/setup/admin", () => {
    return HttpResponse.json(
      { ...CURRENT_USER, role: "admin" },
      { status: 201 },
    );
  }),
];
