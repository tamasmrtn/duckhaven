import { del, get, patch, post } from "./client";
import type { Page } from "./client";
import type { Pat, PatToken, ServiceAccount } from "@/types/service-account";

export interface CreateServiceAccountInput {
  name: string;
  role: string;
}

export interface UpdateServiceAccountInput {
  role?: string;
  is_active?: boolean;
}

export interface IssuePatInput {
  // Days until expiry; null means the PAT never expires.
  expires_in_days: number | null;
}

export const serviceAccountsApi = {
  adminList: async () =>
    (await get<Page<ServiceAccount>>("/admin/service-accounts")).items,

  create: (input: CreateServiceAccountInput) =>
    post<ServiceAccount>("/admin/service-accounts", input),

  update: (id: string, input: UpdateServiceAccountInput) =>
    patch<ServiceAccount>(`/admin/service-accounts/${id}`, input),

  remove: (id: string) => del(`/admin/service-accounts/${id}`),

  listPats: (id: string) => get<Pat[]>(`/admin/service-accounts/${id}/pats`),

  issuePat: (id: string, input: IssuePatInput) =>
    post<PatToken>(`/admin/service-accounts/${id}/pats`, input),

  revokePat: (id: string, patId: string) =>
    del(`/admin/service-accounts/${id}/pats/${patId}`),
};
