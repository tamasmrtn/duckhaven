# Service accounts & access tokens

Every human sign-in flow in DuckHaven — password, [SSO](connect-idp.md), [LDAP](connect-ldap.md) — assumes a person at a
browser. A **service account** is the machine equivalent: a non-human principal that authenticates to the REST API
with a **personal access token (PAT)** instead of a password. Use one whenever an unattended client needs API access
— a CI pipeline, a scheduled job runner, or internal tooling.

A service account is a first-class member of the same access model as a user. It has a
[global role](../concepts/permissions.md) and per-[workspace](../concepts/workspaces.md) membership, and every call it
makes is authorized and audited exactly like a human user's — there is no separate permission system. It simply has no
password and can never complete a browser login.

!!! tip "People can now issue their own tokens"
    A service account is for **unattended** callers. A person who just wants to use
    [the command line](../getting-started/cli-quickstart.md) runs `dh auth login`, which mints a
    token for their own identity — no administrator needed, and no shared credential. Keep service
    accounts for CI, schedulers and tooling, where a token tied to a person would break the day they
    leave.

!!! note "Native tokens only, for now"
    This covers DuckHaven-native PATs, which work with no external identity provider. Federating a service account to an
    Azure Entra ID or AWS IAM identity is planned but **not yet available**.

## Create a service account

**Admin → Service accounts → New service account**. Give it a name (e.g. `ci-runner`) and a global role. It defaults to
the `user` role, which grants **no** global permissions — a new service account can do nothing until you grant it
workspace access, so escalate deliberately.

An email address is generated for it automatically (`<name>@service-account.local`); it is only an internal identifier,
not a real mailbox.

### Grant workspace access

Open the account's **⋯ → Manage workspaces** and give it a role (`reader`, `writer`, or `owner`) in each workspace it
needs, exactly as you would for a user. See [workspace roles](users-access.md#workspace-membership-and-roles).

## Issue an access token

Open the account's **⋯ → Manage tokens → Issue token**. Choose an expiry (30 days, 90 days, 1 year, or never — a bounded
lifetime is recommended so a leaked token eventually stops working) and the token is generated.

!!! warning "Shown only once"
    The token (a `dh_pat_…` string) is displayed **exactly once**, at creation. Copy it into your client's secret store
    immediately — DuckHaven stores only a hash and can never show it again. If you lose it, revoke it and issue a new
    one.

### Use the token

Send it as a bearer token on the `Authorization` header. Query-parameter and cookie transport are intentionally not
supported (they leak into proxy and access logs):

```bash
curl -H "Authorization: Bearer dh_pat_xxxxxxxx" \
  https://<host>/api/me
```

The request resolves to the service account and is subject to the same role and workspace checks as any other caller.

## Rotate and revoke

- **Rotate** — issue a new token, deploy it, then revoke the old one. Tokens are independent, so you can overlap them
  for a zero-downtime rollover.
- **Revoke** — **⋯ → Manage tokens → Revoke** removes a single token immediately; the next request using it gets `401`.
- **Disable the account** — **⋯ → Deactivate** blocks *all* of its tokens at once without deleting anything, and is the
  fastest response if an account is compromised. Reactivate to restore them.
- **Delete the account** — **⋯ → Delete** removes it permanently. An account that has already run queries keeps that
  audit history, so it cannot be deleted (you'll get a conflict); deactivate it instead.

## Auditing

Calls made with a service account's token are recorded against that account in the query
[audit log](../operations/monitoring.md), so machine-driven activity is always attributable to a named principal rather
than to "nobody".
