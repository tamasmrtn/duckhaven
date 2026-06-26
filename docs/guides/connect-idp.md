# Connect an IdP (SSO)

DuckHaven can delegate sign-in to your organization's OpenID Connect identity provider — Okta, Microsoft Entra ID,
Google Workspace, Keycloak, Authentik, and anything else that speaks standard OIDC. Users sign in with a **Sign in with
SSO** button using the Authorization Code + PKCE flow; local accounts keep working alongside it.

## Before you start

Register a **confidential** (server-side) application in your IdP with:

- **Redirect URI** — `https://<your-duckhaven-host>/api/auth/oidc/callback`.
- **Scopes** — `openid email profile`, plus a groups scope/claim if you want
  [group-to-role mapping](#map-groups-to-roles).
- A **client ID** and **client secret**.

Note your IdP's discovery document URL, which ends in `/.well-known/openid-configuration`.

## Configure DuckHaven

Set these in the Compose `.env` (full list in the
[configuration reference](../reference/configuration.md#identity-sso)):

```bash
OIDC_ENABLED=true
OIDC_LABEL="Acme SSO"                    # button text: "Sign in with Acme SSO"
OIDC_SERVER_METADATA_URL=https://idp.example.com/.well-known/openid-configuration
OIDC_CLIENT_ID=duckhaven
OIDC_CLIENT_SECRET=...                   # keep this secret; never commit it
OIDC_REDIRECT_BASE_URL=https://duckhaven.example.com   # required behind a TLS proxy
```

`OIDC_REDIRECT_BASE_URL` must match the host in your registered redirect URI. It is required when DuckHaven runs behind
a reverse proxy (the in-container request can't see the public scheme/host) — see
[Reverse proxy & TLS](../deployment/reverse-proxy-tls.md).

Restart the API. The login page now shows the SSO button.

## Map groups to roles

To let your directory drive who is an admin, expose the user's groups as a claim in the ID token and map group values
to DuckHaven [global roles](../concepts/permissions.md#global-roles-permissions):

```bash
OIDC_GROUPS_CLAIM=groups
OIDC_GROUP_ROLE_MAP={"dh-admins": "admin"}   # JSON object
```

On every sign-in, a user in `dh-admins` becomes an `admin`; everyone else defaults to `user`. Change a person's group
in the IdP and their role updates on their next login — no DuckHaven edit required. Group mapping sets the **global
role only**; workspace membership stays a DuckHaven operation.

## How it works

1. The browser hits `/api/auth/oidc/login`; DuckHaven redirects to your IdP with `state`, `nonce`, and a PKCE
   challenge (stored in a short-lived signed cookie).
2. After the user authenticates, the IdP redirects back to `/api/auth/oidc/callback` with an authorization code.
3. DuckHaven exchanges the code server-to-server, validates the ID token (signature via JWKS, issuer, audience,
   expiry, nonce), then [provisions](../concepts/permissions.md#just-in-time-provisioning) the user and starts a normal
   session.

If anything fails — a bad state, an expired code, or an unreachable IdP — the user is sent back to the login page with
a generic error and can fall back to a local sign-in. Token contents are never logged.

## Troubleshooting

- **`error=sso` on the login page.** Check the API logs for the (non-sensitive) warning, verify the redirect URI and
  `OIDC_REDIRECT_BASE_URL` match exactly, and confirm the discovery URL is reachable from the API container.
- **Everyone is a plain `user`.** The groups claim isn't in the ID token or `OIDC_GROUP_ROLE_MAP` doesn't match the
  group values your IdP emits. Inspect a decoded ID token and align the names.

## Related

- [Identity & permissions](../concepts/permissions.md) · [Connect LDAP / AD](connect-ldap.md) ·
  [Offboarding & break-glass](../operations/offboarding.md)
