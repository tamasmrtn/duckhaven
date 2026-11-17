import { http, HttpResponse } from "msw";
import { currentUser, updateCurrentUser } from "../fixtures/users";
import { httpError } from "../lib/errors";
import { NAV_MODES, THEMES, type User } from "@/types/auth";

export const authHandlers = [
  http.post("/api/auth/login", () => {
    return HttpResponse.json(currentUser());
  }),

  http.post("/api/auth/logout", () => {
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/me", () => {
    return HttpResponse.json(currentUser());
  }),

  http.patch("/api/me", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const unknownField = Object.keys(body).find(
      (k) => k !== "sidebar" && k !== "theme",
    );
    if (unknownField) return httpError(422, `Unknown field: ${unknownField}`);
    if (
      "sidebar" in body &&
      body.sidebar !== null &&
      !(NAV_MODES as readonly unknown[]).includes(body.sidebar)
    ) {
      return httpError(422, "sidebar must be expanded, collapsed or hover");
    }
    if (
      "theme" in body &&
      body.theme !== null &&
      !(THEMES as readonly unknown[]).includes(body.theme)
    ) {
      return httpError(422, "theme must be light, dark or system");
    }
    // Like the server, null puts the theme back to System.
    const changes = { ...body } as Partial<User>;
    if ("theme" in body && body.theme === null) changes.theme = "system";
    return HttpResponse.json(updateCurrentUser(changes));
  }),

  http.get("/api/auth/methods", () => {
    return HttpResponse.json({
      local: true,
      ldap: false,
      oidc_providers: [],
    });
  }),
];
