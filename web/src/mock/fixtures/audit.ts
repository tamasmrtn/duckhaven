import type { AuditEvent } from "@/types/audit";

// Written against a fixed clock, then shifted so the newest sits at "now": the
// Audit page defaults to the last 30 days, and a frozen fixture would leave the
// dev server and every test with an empty page (as for query history).
const FIXTURE_NOW = Date.parse("2026-11-17T12:00:00Z");

const MARTON = { id: "u-1", email: "marton@duckhaven.local" };
const JESS = { id: "u-2", email: "jess@duckhaven.local" };
const ALEX = { id: "u-3", email: "alex@duckhaven.local" };
const ASSISTANT = {
  id: "sa-assistant",
  email: "duckhaven-assistant@service-account.local",
};

type Who = { id: string; email: string };

function event(
  at: string,
  fields: Partial<AuditEvent> & Pick<AuditEvent, "id" | "action" | "label">,
  actor: Who | null = MARTON,
): AuditEvent {
  return {
    event_time: at,
    event_version: 1,
    category: "access",
    outcome: "success",
    actor: {
      id: actor?.id ?? null,
      email: actor?.email ?? null,
      type: actor ? "user" : "anonymous",
      on_behalf_of_id: null,
      on_behalf_of_email: null,
      auth_method: actor ? "session" : null,
      credential_id: actor ? "cred-0001-session" : null,
    },
    request: {
      id: `req-${fields.id}`,
      source_ip: "10.0.4.17",
      user_agent: "Mozilla/5.0",
    },
    workspace: { id: null, slug: null },
    target: { type: "user", id: null, name: null },
    subject: { id: null, email: null },
    self_change: false,
    before: null,
    after: null,
    details: null,
    error_code: null,
    ...fields,
  };
}

const ROWS: AuditEvent[] = [
  event("2026-11-17T11:50:00Z", {
    id: "ae-01",
    action: "workspace_member.add",
    label: "Added a workspace member",
    workspace: { id: "ws-2", slug: "acme-research" },
    target: { type: "workspace_member", id: MARTON.id, name: MARTON.email },
    subject: MARTON,
    self_change: true,
    after: { role: "owner" },
  }),
  event("2026-11-17T11:20:00Z", {
    id: "ae-02",
    action: "catalog_grant.create",
    label: "Granted access to data",
    workspace: { id: "ws-1", slug: "acme-analytics" },
    target: {
      type: "catalog_grant",
      id: "g-41",
      name: "sales.analytics.orders",
    },
    subject: JESS,
    after: { tier: "reader" },
    details: {
      catalog_id: "cat-1",
      catalog: "sales",
      schema: "analytics",
      table: "orders",
    },
  }),
  event(
    "2026-11-17T10:05:00Z",
    {
      id: "ae-03",
      category: "authz",
      action: "authz.grant_denied",
      label: "Was refused by a data grant check",
      outcome: "denied",
      error_code: "grant_denied",
      workspace: { id: "ws-1", slug: "acme-analytics" },
      target: {
        type: "route",
        id: null,
        name: "POST /workspaces/{workspace}/queries",
      },
      details: {
        reason: "Not authorized (reader) on sales.analytics.customers",
        statement: "SELECT * FROM sales.analytics.customers;",
      },
    },
    ASSISTANT,
  ),
  event(
    "2026-11-16T22:40:00Z",
    {
      id: "ae-04",
      category: "auth",
      action: "auth.sign_in_failed",
      label: "Failed to sign in",
      outcome: "failure",
      target: { type: "user", id: null, name: ALEX.email },
      details: { method: "password" },
    },
    null,
  ),
  event(
    "2026-11-16T09:00:00Z",
    {
      id: "ae-05",
      category: "identity",
      action: "user.role_change",
      label: "Changed a user's global role",
      target: { type: "user", id: ALEX.id, name: ALEX.email },
      subject: ALEX,
      before: { role: "user" },
      after: { role: "admin" },
      details: { provider: "oidc:entra" },
    },
    null,
  ),
  event("2026-11-14T15:30:00Z", {
    id: "ae-06",
    action: "workspace_member.role_change",
    label: "Changed a workspace member's role",
    workspace: { id: "ws-1", slug: "acme-analytics" },
    target: { type: "workspace_member", id: JESS.id, name: JESS.email },
    subject: JESS,
    before: { role: "reader" },
    after: { role: "writer" },
  }),
  event(
    "2026-11-12T08:15:00Z",
    {
      id: "ae-07",
      category: "credential",
      action: "pat.revoke",
      label: "Revoked a personal access token",
      target: { type: "pat", id: "pat-7", name: JESS.email },
      subject: JESS,
      self_change: true,
      before: {
        created_at: "2026-08-14T08:00:00Z",
        expires_at: "2027-08-14T08:00:00Z",
      },
    },
    JESS,
  ),
  event("2026-11-03T13:00:00Z", {
    id: "ae-08",
    action: "catalog.access_mode_change",
    label: "Changed a catalog's access mode",
    workspace: { id: "ws-1", slug: "acme-analytics" },
    target: { type: "catalog", id: "cat-1", name: "sales" },
    before: { access_mode: "open" },
    after: { access_mode: "scoped" },
  }),
];

// The delegated call: the assistant's account acted, for Jess.
ROWS[2] = {
  ...ROWS[2],
  actor: {
    ...ROWS[2].actor,
    type: "service_account",
    auth_method: "pat",
    on_behalf_of_id: JESS.id,
    on_behalf_of_email: JESS.email,
  },
};
// An identity provider changed the role at sign-in: no account acted.
ROWS[4] = { ...ROWS[4], actor: { ...ROWS[4].actor, type: "idp" } };

/** Every event, newest first, shifted so the newest is now. */
export function auditEvents(): AuditEvent[] {
  const delta = Date.now() - FIXTURE_NOW;
  return ROWS.map((e) => ({
    ...e,
    event_time: new Date(Date.parse(e.event_time) + delta).toISOString(),
  }));
}
