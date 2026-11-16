import { http, HttpResponse } from "msw";
import { currentUser, updateCurrentUser } from "../fixtures/users";
import { httpError } from "../lib/errors";
import { NAV_MODES } from "@/types/auth";

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
    const unknownField = Object.keys(body).find((k) => k !== "sidebar");
    if (unknownField) return httpError(422, `Unknown field: ${unknownField}`);
    if (
      "sidebar" in body &&
      body.sidebar !== null &&
      !(NAV_MODES as readonly unknown[]).includes(body.sidebar)
    ) {
      return httpError(422, "sidebar must be expanded, collapsed or hover");
    }
    return HttpResponse.json(
      updateCurrentUser(
        body as { sidebar?: (typeof NAV_MODES)[number] | null },
      ),
    );
  }),

  http.get("/api/auth/methods", () => {
    return HttpResponse.json({
      local: true,
      ldap: false,
      oidc_providers: [],
    });
  }),
];
