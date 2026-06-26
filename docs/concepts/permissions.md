# Identity & permissions

DuckHaven separates **who you are** (identity) from **what you can do** (authorization). Authorization is always
enforced at the API boundary before any query reaches an [agent](agents.md) — DuckHaven is the sole permission
authority.

## Identity (how you sign in)

An account authenticates one of three ways, recorded on the user as its `auth_provider`:

- **Local** — email and a bcrypt-hashed password. Always available, so a break-glass admin can sign in even when an
  external IdP is unreachable.
- **OIDC SSO** — the organization's identity provider (Okta, Microsoft Entra ID, Google Workspace, Keycloak,
  Authentik, …) via the standard Authorization Code + PKCE flow. See [Connect an IdP (SSO)](../guides/connect-idp.md).
- **LDAP / Active Directory** — a directory bind, used as a secondary path. See
  [Connect LDAP / AD](../guides/connect-ldap.md).

Local accounts always coexist with SSO/LDAP. When you submit a password, DuckHaven verifies it against a **local**
account first (the break-glass path); only if there is no local password for that email does it try an LDAP bind. OIDC
is a separate "Sign in with SSO" button. A session is the same in every case: an opaque, server-stored cookie
(`session`), so logout and expiry behave identically regardless of how you signed in.

### Just-in-time provisioning

The first time someone signs in through SSO or LDAP, DuckHaven creates their account automatically (matched by email)
— no manual pre-provisioning. On each subsequent sign-in their name and [role](#global-roles-permissions) are
re-synced from the IdP, so the directory stays authoritative. An email already registered to a *different* provider is
refused, which prevents a federated identity from taking over the local admin.

## Global roles & permissions

Every user has one **global role** backing a set of enumerated **permissions**. DuckHaven ships two built-in roles:

| Role | Permissions | Meaning |
|---|---|---|
| `admin` | all | Manage agents, storage backends, users, and maintenance; administer any catalog and the full query log. |
| `user` | none (global) | A normal account. Workspace access is granted separately (below). |

Permissions are checked individually at each admin endpoint (for example `users:manage` gates the user-management
APIs), so the model reads cleanly in a security review rather than hiding behind a single "is admin" flag.

### Mapping IdP groups to roles

For SSO/LDAP users, the global role is derived from the IdP's group claims on every sign-in via a configured mapping
(for example `dh-admins → admin`). This makes onboarding and offboarding a directory operation: add someone to the
admin group in your IdP and they become an admin on next login; remove them and they are demoted. Group membership maps
to the **global role only** — workspace membership remains an explicit DuckHaven operation.

## Workspace roles

Independently of the global role, each member of a [workspace](workspaces.md) has one role:

| Role | Can do |
|---|---|
| `reader` | List and browse schemas and tables; run queries; view history |
| `writer` | Everything a reader can, plus create/modify/drop tables and run DDL |
| `owner` | Everything a writer can, plus manage workspace membership |

## What is not in scope

- **No row- or column-level security.** Permissions are workspace-level.
- **No SCIM.** Provisioning is just-in-time at login, not a push from the directory.
- **RBAC is API-enforced only.** Roles and workspace membership are enforced by DuckHaven; Polaris sees only the API
  service principal and is never granted per-user access. The catalog grant mirror is intentionally a no-op.

## Related

- [Manage users & access](../guides/users-access.md) — create users, assign roles, offboard.
- [Connect an IdP (SSO)](../guides/connect-idp.md) and [Connect LDAP / AD](../guides/connect-ldap.md).
- [Offboarding & break-glass](../operations/offboarding.md) — revoke access and recover from IdP outages.
