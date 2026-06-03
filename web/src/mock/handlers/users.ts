import { http, HttpResponse } from "msw";
import { ALL_USERS } from "../fixtures/users";

export const userHandlers = [
  http.get("/api/admin/users", () => {
    return HttpResponse.json(ALL_USERS);
  }),
];
