import { get, type Page } from "./client";
import type { AuditCategory, AuditEvent, AuditOutcome } from "@/types/audit";

export interface AuditEventsParams {
  since?: string;
  until?: string;
  category?: AuditCategory;
  /** An action name, or a prefix ending in `.*`. */
  action?: string;
  /** Events this account made, or that were made on its behalf. */
  actor_id?: string;
  /** Events that changed this account's access. */
  subject_id?: string;
  /** A workspace slug or id. */
  workspace?: string;
  outcome?: AuditOutcome;
  self_change?: boolean;
  limit?: number;
  cursor?: string;
}

export const auditApi = {
  listEvents: (params: AuditEventsParams = {}) => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value != null && value !== "") qs.set(key, String(value));
    }
    const suffix = qs.toString();
    return get<Page<AuditEvent>>(
      `/admin/audit/events${suffix ? `?${suffix}` : ""}`,
    );
  },
};
