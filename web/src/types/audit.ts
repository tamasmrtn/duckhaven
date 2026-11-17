/** The access audit log, as `GET /api/admin/audit/events` returns it. */

export const AUDIT_CATEGORIES = [
  "identity",
  "access",
  "credential",
  "auth",
  "authz",
] as const;
export type AuditCategory = (typeof AUDIT_CATEGORIES)[number];

export const AUDIT_OUTCOMES = ["success", "denied", "failure"] as const;
export type AuditOutcome = (typeof AUDIT_OUTCOMES)[number];

export type AuditActorType =
  "user" | "service_account" | "system" | "idp" | "anonymous";

export interface AuditEvent {
  id: string;
  event_time: string;
  event_version: number;
  category: AuditCategory;
  action: string;
  /** A short description of the action, for display. */
  label: string;
  outcome: AuditOutcome;
  actor: {
    id: string | null;
    email: string | null;
    type: AuditActorType;
    /** The person a delegated call (the assistant's) acted for. */
    on_behalf_of_id: string | null;
    on_behalf_of_email: string | null;
    auth_method: "session" | "pat" | "system" | null;
    credential_id: string | null;
  };
  request: {
    id: string | null;
    source_ip: string | null;
    user_agent: string | null;
  };
  /** As it was at the time; kept after the workspace is deleted. */
  workspace: { id: string | null; slug: string | null };
  target: { type: string; id: string | null; name: string | null };
  /** Whose access changed. */
  subject: { id: string | null; email: string | null };
  /** The subject is the actor, or the person a delegated call acted for. */
  self_change: boolean;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  details: Record<string, unknown> | null;
  error_code: string | null;
}
