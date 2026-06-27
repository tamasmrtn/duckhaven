# Connect an IdP (SSO)

DuckHaven can delegate sign-in to one or more OpenID Connect identity providers — Microsoft Entra ID, Okta, Authentik,
Keycloak, AWS Cognito, Google Workspace, and anything else that speaks standard OIDC. Each configured provider becomes
its own **Sign in with …** button on the login page (Authorization Code + PKCE); local accounts keep working alongside
them.

## Before you start

For each provider, register a **confidential** (server-side) application in that IdP with:

- **Redirect URI** — `https://<your-duckhaven-host>/api/auth/oidc/<id>/callback`, where `<id>` is the short slug you
  give the provider in DuckHaven (e.g. `entra`, `okta`, `authentik`).
- **Scopes** — `openid email profile` (plus a groups claim if you want [group-to-role mapping](#map-groups-to-roles)).
- A **client ID** and **client secret**.

Note each IdP's discovery document URL, which ends in `/.well-known/openid-configuration`.

## Configure DuckHaven

Set one `OIDC_PROVIDERS` entry per IdP — a JSON list — in the Compose `.env` (full list in the
[configuration reference](../reference/configuration.md#identity-sso)):

```bash
OIDC_PROVIDERS='[
  {
    "id": "entra",
    "label": "Microsoft",
    "server_metadata_url": "https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration",
    "client_id": "...",
    "client_secret": "...",
    "scopes": "openid email profile",
    "group_role_map": {"<entra-group-object-id>": "admin"}
  },
  {
    "id": "okta",
    "label": "Okta",
    "server_metadata_url": "https://<org>.okta.com/.well-known/openid-configuration",
    "client_id": "...",
    "client_secret": "..."
  }
]'
OIDC_REDIRECT_BASE_URL=https://duckhaven.example.com   # required behind a TLS proxy
```

Each provider's `id` is the slug used in its callback path (`/api/auth/oidc/<id>/callback`); `label` is the button
text. `OIDC_REDIRECT_BASE_URL` must match the host in your registered redirect URIs — it is required behind a reverse
proxy (the in-container request can't see the public scheme/host); see
[Reverse proxy & TLS](../deployment/reverse-proxy-tls.md). Restart the API; the login page now shows a button per
provider.

!!! note "Single-provider shorthand (back-compat)"
    The older single-provider variables (`OIDC_ENABLED`, `OIDC_SERVER_METADATA_URL`, `OIDC_CLIENT_ID`,
    `OIDC_CLIENT_SECRET`, `OIDC_SCOPES`, `OIDC_GROUP_ROLE_MAP`) still work: when set and `OIDC_PROVIDERS` is empty they
    synthesize one provider with id `sso` (callback `/api/auth/oidc/sso/callback`). `OIDC_PROVIDERS` takes precedence.

## Provider presets

All speak standard OIDC, so only the discovery URL and the app-registration knobs differ:

- **Microsoft Entra ID** — discovery is tenant-scoped:
  `https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration`. **Do not** add `groups` to
  `scopes` (not a valid Entra scope — sign-in fails with `invalid_scope`); Entra emits group claims from the app
  registration's **groupMembershipClaims** (e.g. *SecurityGroup*) and puts group **object IDs** (GUIDs) in the `groups`
  claim, so `group_role_map` keys are those GUIDs. Entra omits the `email` claim for some accounts — DuckHaven falls
  back to `preferred_username`.
- **Authentik / Keycloak** — self-hosted; discovery is
  `https://<host>/application/o/<app>/.well-known/openid-configuration` (Authentik) or
  `https://<host>/realms/<realm>/.well-known/openid-configuration` (Keycloak). Add a groups mapper to the client so the
  `groups` claim is emitted.
- **Okta** — discovery `https://<org>.okta.com/.well-known/openid-configuration` (or an auth-server-scoped variant).
- **AWS Cognito** — discovery
  `https://cognito-idp.<region>.amazonaws.com/<user-pool-id>/.well-known/openid-configuration`. Cognito groups arrive
  in the `cognito:groups` claim — set that provider's `groups_claim` accordingly.

## Map groups to roles

To let your directory drive who is an admin, expose the user's groups as a claim and map group values to DuckHaven
[global roles](../concepts/permissions.md#global-roles-permissions) per provider:

```json
{ "groups_claim": "groups", "group_role_map": {"dh-admins": "admin"} }
```

On every sign-in, a user in a mapped group gets that role (`admin` wins when several match); everyone else defaults to
`user`. Change a person's group in the IdP and their role updates on their next login. Group mapping sets the **global
role only**; workspace membership stays a DuckHaven operation.

## How it works

1. The browser hits `/api/auth/oidc/<id>/login`; DuckHaven redirects to that provider's IdP with `state`, `nonce`, and
   a PKCE challenge (stored in a short-lived signed cookie).
2. After the user authenticates, the IdP redirects back to `/api/auth/oidc/<id>/callback` with an authorization code.
3. DuckHaven exchanges the code server-to-server, validates the ID token (signature via JWKS, issuer, audience, expiry,
   nonce), resolves the identity (`email`, falling back to `preferred_username`/`upn`), then
   [provisions](../concepts/permissions.md#just-in-time-provisioning) the user and starts a normal session.

If anything fails — a bad state, an expired code, missing identity claims, or an unreachable IdP — the user is sent
back to the login page with a generic error and can fall back to a local sign-in. Token contents are never logged.

## Troubleshooting

- **`error=sso` on the login page.** Check the API logs for the (non-sensitive) warning — it names the provider and,
  for a missing-claim failure, the claim names present in the token. Verify the redirect URI and
  `OIDC_REDIRECT_BASE_URL` match exactly, and that the discovery URL is reachable from the API container.
- **Everyone is a plain `user`.** The groups claim isn't in the ID token or `group_role_map` doesn't match the values
  your IdP emits (Entra uses group GUIDs; Cognito uses `cognito:groups`). Inspect a decoded ID token and align them.

## Related

- [Identity & permissions](../concepts/permissions.md) · [Connect LDAP / AD](connect-ldap.md) ·
  [Offboarding & break-glass](../operations/offboarding.md)
