import { del, get, patch, put } from "./client";
import type {
  AccessMode,
  CatalogGrants,
  Grant,
  GrantUpsertInput,
} from "@/types/grant";

const base = (ws: string, catalog: string) =>
  `/workspaces/${ws}/catalogs/${catalog}`;

export const grantsApi = {
  list: (ws: string, catalog: string) =>
    get<CatalogGrants>(`${base(ws, catalog)}/grants`),

  setAccessMode: (ws: string, catalog: string, access_mode: AccessMode) =>
    patch<CatalogGrants>(`${base(ws, catalog)}/access-mode`, { access_mode }),

  upsert: (ws: string, catalog: string, input: GrantUpsertInput) =>
    put<Grant>(`${base(ws, catalog)}/grants`, input),

  remove: (ws: string, catalog: string, grantId: string) =>
    del(`${base(ws, catalog)}/grants/${grantId}`),
};
