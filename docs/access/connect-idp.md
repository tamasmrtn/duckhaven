---
type: how-to
---

# Connect an IdP (SSO)

Let people sign in to DuckHaven through your OpenID Connect identity provider, such as Microsoft Entra ID, Okta,
Keycloak, Authentik, AWS Cognito or Google Workspace.

Each provider you configure becomes its own **Sign in with …** button on the login page. Local accounts keep working
alongside them, so an admin can still sign in when the provider is down.

## Before you begin

- **You need:** shell access to the DuckHaven host, to edit its `.env`, and an administrator of the identity provider
  to register an application.
- **Applies to:** every deployment. Behind a reverse proxy, DuckHaven must be reachable at the HTTPS address users see.

For each provider, register a **confidential** (server-side) application in that IdP with:

- **Redirect URI** — `https://<your-duckhaven-host>/api/auth/oidc/<id>/callback`, where `<id>` is the short slug you
  give the provider in DuckHaven (for example `entra`, `okta` or `authentik`).
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
[Reverse proxy & TLS](../deployment/reverse-proxy-tls.md). Apply the change with `docker compose up -d`, which recreates
the API container with the new settings.

!!! note "Single-provider shorthand"
    For one provider you can use the single-provider variables (`OIDC_ENABLED`, `OIDC_SERVER_METADATA_URL`,
    `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SCOPES`, `OIDC_GROUP_ROLE_MAP`) instead. When `OIDC_PROVIDERS` is
    empty they define one provider with id `sso` (callback `/api/auth/oidc/sso/callback`); `OIDC_PROVIDERS` wins when
    both are set.

## Provider presets

All speak standard OIDC, so only the discovery URL and the app-registration knobs differ:

- **Microsoft Entra ID** — discovery is tenant-scoped:
  `https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration`. **Do not** add `groups` to
  `scopes` (not a valid Entra scope — sign-in fails with `invalid_scope`); Entra emits group claims from the app
  registration's **groupMembershipClaims** (for example *SecurityGroup*) and puts group **object IDs** (GUIDs) in the
  `groups` claim, so `group_role_map` keys are those GUIDs. Entra omits the `email` claim for some accounts — DuckHaven
  falls back to `preferred_username`.
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
[global roles](../reference/permissions.md#global-permissions) per provider:

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
   [provisions](how-access-works.md#signing-in) the user and starts a normal session.

If anything fails — a bad state, an expired code, missing identity claims, or an unreachable IdP — the user is sent
back to the login page with a generic error and can sign in locally instead. Token contents are never logged.

## Check that it worked

Open the login page: there is a **Sign in with ⟨label⟩** button per provider. Sign in with a test account. You land in
DuckHaven, and **Admin** → **Users** lists the account with the role its groups map to.

## If it goes wrong

### The login page says "Single sign-on failed. Try again or sign in locally."

The sign-in came back with `?error=sso`. The API log has a warning naming the provider and, when an identity claim was
missing, the claims the token did contain (`docker compose logs api`). Check that the registered redirect URI and
`OIDC_REDIRECT_BASE_URL` match exactly, and that the API container can reach the discovery URL.

### Sign-in fails with `invalid_scope` on Microsoft Entra ID

`groups` is in the provider's `scopes`. Entra does not accept it as a scope; remove it and configure group claims in the
app registration instead.

### Everyone signs in as a plain `user`

The ID token has no groups claim, or `group_role_map` does not match the values your provider sends — Entra sends
group object IDs, and Cognito uses `cognito:groups`. Decode a test user's ID token and align the two.

### The **Sign in with …** button does not appear

The API did not pick up the settings. Run `docker compose up -d` so the container is recreated, and check that
`OIDC_PROVIDERS` is valid JSON.

## Related

- [How access works](how-access-works.md#signing-in) — how sign-in methods combine.
- [Connect LDAP / AD](connect-ldap.md) — the directory alternative.
- [Offboarding & break-glass](offboarding.md) — removing access, and signing in when the provider is down.
