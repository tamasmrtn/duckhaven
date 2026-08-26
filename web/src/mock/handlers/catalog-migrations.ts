import { http, HttpResponse } from "msw";

import { page } from "../lib/page";

import { httpError } from "../lib/errors";
import { nextId } from "../lib/seed";
import {
  addMigration,
  eventsFor,
  findMigration,
  migrationsFor,
  newMigration,
} from "../fixtures/catalog-migrations";

export const catalogMigrationHandlers = [
  http.get("/api/catalogs/:catalogId/migrations", ({ params }) =>
    page(migrationsFor(params.catalogId as string)),
  ),

  http.post(
    "/api/catalogs/:catalogId/migrations",
    async ({ params, request }) => {
      const body = (await request.json()) as {
        target_storage_backend_id: string;
      };
      const migration = newMigration(
        nextId("mig"),
        params.catalogId as string,
        body.target_storage_backend_id,
      );
      addMigration(migration);
      return HttpResponse.json(migration, { status: 202 });
    },
  ),

  http.get("/api/catalogs/:catalogId/migrations/:id", ({ params }) => {
    const m = findMigration(params.id as string);
    return m ? HttpResponse.json(m) : httpError(404, "Migration not found");
  }),

  http.get(
    "/api/catalogs/:catalogId/migrations/:id/logs",
    ({ params, request }) => {
      const after = Number(new URL(request.url).searchParams.get("after") ?? 0);
      return HttpResponse.json(
        eventsFor(params.id as string).filter((e) => e.seq > after),
      );
    },
  ),

  http.post("/api/catalogs/:catalogId/migrations/:id/cancel", ({ params }) => {
    const m = findMigration(params.id as string);
    if (!m) {
      return httpError(404, "Migration not found");
    }
    return HttpResponse.json({ ...m });
  }),
];
