import { http, HttpResponse } from "msw";
import { CURRENT_USER } from "../fixtures/users";

export const authHandlers = [
  http.post("/api/auth/login", () => {
    return HttpResponse.json(CURRENT_USER);
  }),

  http.post("/api/auth/logout", () => {
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/me", () => {
    return HttpResponse.json(CURRENT_USER);
  }),

  http.get("/api/auth/methods", () => {
    return HttpResponse.json({
      local: true,
      ldap: false,
      oidc: false,
      oidc_label: "SSO",
    });
  }),
];
