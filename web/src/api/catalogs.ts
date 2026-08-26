import { del, get, post, put } from "./client";
import type { Catalog } from "@/types/catalog";
import type { AccessMode } from "@/types/grant";

export const catalogsApi = {
  // Catalogs attached to a workspace (the workspace → catalog tree level).
  listForWorkspace: (ws: string) =>
    get<Catalog[]>(`/workspaces/${ws}/catalogs`),

  // Every catalog in the deployment — the attach picker's source.
  listAll: () => get<Catalog[]>(`/catalogs`),

  create: (
    ws: string,
    body: {
      name: string;
      storage_backend_id?: string;
      access_mode?: AccessMode;
    },
  ) => post<Catalog>(`/workspaces/${ws}/catalogs`, body),

  // Attach an existing catalog to a workspace (M:N sharing). A PUT on the
  // attachment's own address — the one detach deletes — so it is idempotent.
  attach: (ws: string, catalog: string, makeDefault = false) =>
    put<Catalog>(`/workspaces/${ws}/catalogs/${catalog}`, {
      make_default: makeDefault,
    }),

  detach: (ws: string, catalog: string) =>
    del(`/workspaces/${ws}/catalogs/${catalog}`),

  // Permanently delete a catalog (refused while attached to any workspace).
  drop: (catalogId: string) => del(`/catalogs/${catalogId}`),
};
