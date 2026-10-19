---
type: how-to
---

# Manage users & access

Add people to DuckHaven, give them a global role, and give them a role in the workspaces they work in.

These are the first two of DuckHaven's [four access layers](how-access-works.md). Narrower access inside a catalog is
covered by [Scoped access](scoped-access.md), and reserving compute by [Agent access](agent-access.md).

## Before you begin

- **You need:** the `users:manage` permission, which the `admin` role holds. A workspace `owner` can also manage the
  members of their own workspace.
- **Applies to:** local accounts and accounts from single sign-on or LDAP alike. The first admin is created during
  [installation](../getting-started/quickstart.md#2-create-the-first-admin).

## Add a user

People who sign in through [single sign-on](connect-idp.md) or [LDAP](connect-ldap.md) get an account automatically the
first time they sign in; skip to [giving them a workspace](#give-someone-access-to-a-workspace). For a local account:

1. Open **Admin** → **Users** → **New user**.
2. Enter their **Email**, **Name** and a **Temporary password**, and choose a **Role**: `user` for everyone who does not
   administer DuckHaven, `admin` for those who do.
3. Click **Create user**, and send them the password through a channel you trust.

The new account appears in the list, but it can reach no data until it has a workspace.

## Give someone access to a workspace

1. In **Admin** → **Users**, open the person's **⋯** → **Manage workspaces**.
2. Choose their role in each workspace: `reader`, `writer` or `owner`. The change saves immediately, and **No access**
   removes them.

What each role allows is in the [permissions reference](../reference/permissions.md#workspace-roles). A workspace
`owner` can manage the same memberships for their own workspace without `users:manage`.

Workspace membership is always set in DuckHaven. Identity-provider groups decide only the global role.

## Change someone's global role

Use the **Role for ⟨name⟩** selector on their row. For people who sign in through single sign-on or LDAP, the role is
reset from their directory groups on their next sign-in, so change it there instead; see
[Connect an IdP](connect-idp.md#map-groups-to-roles).

## Sign someone out, or switch an account off

The person's **⋯** menu has two actions:

- **Revoke sessions** signs them out everywhere. They can sign in again. It does not stop personal access tokens.
- **Deactivate** blocks the account: every session and token is refused on its next request, and they cannot sign in
  again until reactivated.

For someone leaving, follow [Offboarding & break-glass](offboarding.md).

## Check that it worked

Ask the person to sign in and open the workspace. They see its catalogs in the catalog tree, and **Admin** appears in
their navigation only if they are an `admin`.

## If it goes wrong

### Someone's role changes back after they sign in

They sign in through single sign-on or LDAP, and their directory groups map to a different role. Change their groups
in the identity provider.

### A deactivate or demote is refused

DuckHaven never lets the last active admin be deactivated or demoted, so it cannot lock itself out. Make another
account an admin first.

### Someone sees the workspace but not a catalog's tables

The catalog is scoped and they have no grant on it. See [Scoped access](scoped-access.md).

## Related

- [How access works](how-access-works.md) — the four layers.
- [Service accounts & tokens](service-accounts.md) — accounts for automation.
- [Offboarding & break-glass](offboarding.md) — removing access cleanly.
