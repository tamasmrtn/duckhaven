import { http, HttpResponse } from "msw";
import type { AccessMode, Grant, GrantTier } from "@/types/grant";
import { ACCESS_MODE, GRANT_PRINCIPALS, GRANTS } from "../fixtures/grants";
import { httpError } from "../lib/errors";
import { nextId } from "../lib/seed";

const B = "/api/workspaces/:ws/catalogs/:catalog";

function payload(catalog: string) {
  return {
    access_mode: ACCESS_MODE[catalog] ?? "open",
    grants: GRANTS[catalog] ?? [],
    principals: GRANT_PRINCIPALS,
  };
}

export const grantHandlers = [
  http.get(`${B}/grants`, ({ params }) => {
    return HttpResponse.json(payload(params.catalog as string));
  }),

  http.patch(`${B}/access-mode`, async ({ params, request }) => {
    const catalog = params.catalog as string;
    const body = (await request.json()) as { access_mode: AccessMode };
    ACCESS_MODE[catalog] = body.access_mode;
    return HttpResponse.json(payload(catalog));
  }),

  http.put(`${B}/grants`, async ({ params, request }) => {
    const catalog = params.catalog as string;
    const body = (await request.json()) as {
      user_id: string;
      schema_name?: string | null;
      table_name?: string | null;
      tier: GrantTier;
    };
    if (body.table_name && !body.schema_name) {
      return httpError(422, "table_name requires schema_name");
    }
    const principal = GRANT_PRINCIPALS.find((p) => p.user_id === body.user_id);
    if (!principal) {
      return httpError(422, "Principal is not a member of this workspace.");
    }
    const list = (GRANTS[catalog] ??= []);
    const schema = body.schema_name ?? null;
    const table = body.table_name ?? null;
    const existing = list.find(
      (g) =>
        g.user_id === body.user_id &&
        g.schema_name === schema &&
        g.table_name === table,
    );
    if (existing) {
      existing.tier = body.tier;
      return HttpResponse.json(existing);
    }
    const grant: Grant = {
      id: nextId("grant"),
      user_id: body.user_id,
      user_name: principal.name,
      schema_name: schema,
      table_name: table,
      tier: body.tier,
      created_at: new Date().toISOString(),
    };
    list.push(grant);
    return HttpResponse.json(grant, { status: 201 });
  }),

  http.delete(`${B}/grants/:grantId`, ({ params }) => {
    const catalog = params.catalog as string;
    const list = GRANTS[catalog] ?? [];
    GRANTS[catalog] = list.filter((g) => g.id !== params.grantId);
    return new HttpResponse(null, { status: 204 });
  }),
];
