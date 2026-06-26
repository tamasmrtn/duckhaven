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

`Revoke sessions` (in the same **⋯** menu) force-logs-out a user without disabling the account — useful for a lost or
stolen device when the person is staying.

The last remaining active admin cannot be demoted or deactivated, so you can never lock the system out of its own
administration.

!!! note "Sessions are server-side"
    A DuckHaven session is an opaque, server-stored token. Deactivation and `Revoke sessions` take effect on the
    account's next request — there is no signed token that keeps working until it expires. Session lifetime itself is
    bounded by `SESSION_MAX_AGE_SECONDS` (see the
    [configuration reference](../reference/configuration.md#identity-sso)).

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
