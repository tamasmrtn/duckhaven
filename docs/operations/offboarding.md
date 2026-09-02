# Offboarding & break-glass

How to remove someone's access cleanly, and how to stay in control when your identity provider is down.

## Offboard a user

When someone leaves, what you do depends on how they signed in:

- **SSO / LDAP user** — disable or remove them in your **identity provider** (or remove them from the mapped admin
  group). DuckHaven re-checks the directory on every sign-in, so they can no longer authenticate and an admin group
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

There is currently no operator view of a user's own tokens — `Admin → Service accounts` covers service accounts only. A
user can list and revoke their own with `dh auth tokens` and `dh auth revoke`, so where the person is cooperative and
merely changing machines, ask them to do that.

The last remaining active admin cannot be demoted or deactivated, so you can never lock the system out of its own
administration.

!!! note "Sessions are server-side"
    A DuckHaven session is an opaque, server-stored token, so deactivation and `Revoke sessions` take effect on the
    account's next request rather than whenever a signed token would have expired. Session lifetime is bounded by
    `SESSION_MAX_AGE_SECONDS` (see the [configuration reference](../reference/configuration.md#identity-sso)).
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

## Related

- [Manage users & access](../guides/users-access.md) · [Identity & permissions](../concepts/permissions.md)
- [Connect an IdP (SSO)](../guides/connect-idp.md) · [Connect LDAP / AD](../guides/connect-ldap.md)
