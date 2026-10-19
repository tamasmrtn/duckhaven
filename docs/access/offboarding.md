---
type: how-to
---

# Offboarding & break-glass

Remove someone's access cleanly when they leave, and keep a way in when your identity provider is down.

## Before you begin

- **You need:** the `users:manage` permission, which the `admin` role holds.
- **Applies to:** local accounts and accounts from single sign-on or LDAP.

## Offboard a user

When someone leaves, what you do depends on how they signed in:

- **SSO / LDAP user** — disable or remove them in your **identity provider** (or remove them from the mapped admin
  group). DuckHaven re-checks the directory on every sign-in, so they cannot sign in and an admin group
  removal demotes them. To cut off any *currently live* DuckHaven session immediately rather than waiting for it to
  expire, also **Deactivate** them in **Admin → Users**.
- **Local user** — **Deactivate** them in **Admin → Users**. This blocks new sign-ins and rejects their existing
  session on its next request.

`Revoke sessions` (in the same **⋯** menu) force-logs-out a user without disabling the account.

!!! warning "`Revoke sessions` does not revoke access tokens"
    It deletes browser sessions only. A user who has issued themselves a
    [personal access token](../reference/rest-api.md#managing-your-own-tokens) — with `dh auth login`, for instance —
    keeps a working credential for as long as that token lives, which is up to a year.

**For a lost or stolen device, deactivate the account.** Deactivation is the only action that stops a user's tokens, and
it takes effect on their next request. `Revoke sessions` on its own is enough only when you know the person holds no
tokens.

DuckHaven has no operator view of a user's own tokens: **Admin** → **Service accounts** covers service accounts only.
A user can list and revoke their own with `dh auth tokens` and `dh auth revoke`, so when the person is only changing
machines, ask them to do that.

The last remaining active admin cannot be demoted or deactivated, so you can never lock the system out of its own
administration.

!!! note "Sessions are server-side"
    A DuckHaven session is an opaque, server-stored token, so deactivation and `Revoke sessions` take effect on the
    account's next request rather than whenever a signed token would have expired. Session lifetime is bounded by
    `SESSION_MAX_AGE_SECONDS` (see the
    [configuration reference](../reference/configuration.md#control-plane-compose-env)).
    Personal access tokens are opaque and server-stored too, but `Revoke sessions` does not touch them — see above.

## Break-glass: signing in when the IdP is down

DuckHaven always keeps **local** authentication available, so an outage of your OIDC provider or LDAP directory never
locks you out:

- Keep at least one **local admin** account (the first admin created at install is local). Store its password in your
  team's secret manager.
- A submitted password is verified against a local account **first**, before any LDAP bind — so the local admin signs
  in even when the directory is unreachable.
- If SSO is misbehaving, sign in with the local admin using the email/password form (skip the "Sign in with SSO"
  button), then investigate from **Admin**.

Treat the break-glass admin like any other privileged credential: rotate it periodically and audit its use.

## If it goes wrong

### Deactivate or demote is refused for an admin

DuckHaven never lets the last active admin be deactivated or demoted. Make another account an admin first.

### A deactivated person's single sign-on still works

It does not: a deactivated account is refused however it signs in. If they can still use DuckHaven, check that you
deactivated the account they actually use; someone with a local account and a directory account with a different email
has two.

## Related

- [Manage users & access](manage-users.md) — roles and memberships.
- [How access works](how-access-works.md#signing-in) — how sign-in methods combine.
- [Connect an IdP (SSO)](connect-idp.md) and [Connect LDAP / AD](connect-ldap.md).
