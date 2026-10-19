---
type: how-to
---

# Connect LDAP / AD

Let people sign in to DuckHaven with their LDAP or Active Directory credentials on the normal login form.

DuckHaven checks the password against the directory and creates the account on first sign-in. For most organizations
[single sign-on](connect-idp.md) is the better primary path; LDAP suits directories without an OIDC provider.

## Before you begin

- **You need:** shell access to the DuckHaven host, to edit its `.env`, and a read-only directory service account.
- **Applies to:** every deployment. The directory must be reachable from the API container, over TLS.

## How sign-in is routed

When you submit the login form, DuckHaven checks for a **local** account with that email first (the break-glass path),
and only falls back to an LDAP bind if there is none. So enabling LDAP never locks out the local admin, even if the
directory is unreachable.

## Configure DuckHaven

Set these in the Compose `.env` (full list in the
[configuration reference](../reference/configuration.md#identity-sso)):

```bash
LDAP_ENABLED=true
LDAP_SERVER_URI=ldaps://dc.example.com        # ldaps:// (port 636) or ldap:// with STARTTLS
LDAP_USE_START_TLS=false                       # true to upgrade an ldap:// connection to TLS
LDAP_BIND_DN=cn=svc-duckhaven,ou=svc,dc=example,dc=com   # read-only service account
LDAP_BIND_PASSWORD=...
LDAP_USER_SEARCH_BASE=ou=people,dc=example,dc=com
LDAP_USER_FILTER=(mail={email})                # {email} is substituted (and escaped)
LDAP_EMAIL_ATTR=mail
LDAP_NAME_ATTR=displayName
LDAP_GROUP_ATTR=memberOf
LDAP_TLS_CA_CERT=/etc/ssl/certs/corp-ca.pem    # CA bundle for the directory's TLS cert
```

Apply the change with `docker compose up -d`, which recreates the API container with the new settings.

DuckHaven binds as the service account, searches for the user, then **re-binds as that user** with the submitted
password to verify it. For Active Directory, a common filter is `(sAMAccountName={email})` or
`(userPrincipalName={email})`.

### Map groups to roles

DuckHaven reads the user's `memberOf` values and maps group DNs to
[global roles](../reference/permissions.md#global-permissions):

```bash
LDAP_GROUP_ROLE_MAP={"cn=dh-admins,ou=groups,dc=example,dc=com": "admin"}
```

Members of that group become `admin` on each sign-in; everyone else defaults to `user`.

## Operational notes

- **Always use TLS.** Prefer `ldaps://` or STARTTLS with a pinned CA (`LDAP_TLS_CA_CERT`); certificates are validated.
- **Use a read-only service account** for the search bind and rotate its password regularly.
- **Watch for lockouts.** Repeated failed user binds count against directory lockout policy. DuckHaven does not retry a
  failed bind.
## Check that it worked

Sign in on the login page with a directory user's email and password. You land in DuckHaven, and **Admin** →
**Users** lists the account with the role its groups map to.

## If it goes wrong

### The login form says "Invalid credentials."

A wrong password, a user the search did not find, and a directory that did not answer in time all look the same to
the user, by design. Check the API log (`docker compose logs api`) and confirm that `LDAP_USER_FILTER` finds the user
when you run the same search with `ldapsearch` as the service account.

### Everyone signs in as a plain `user`

`LDAP_GROUP_ROLE_MAP` keys must match the full group DNs in the user's `memberOf` values exactly.

### Users get locked out in the directory

Every failed sign-in is a failed bind, which counts against the directory's lockout policy. DuckHaven does not retry a
failed bind, so repeated lockouts point at people retrying a wrong password.

## Related

- [How access works](how-access-works.md#signing-in) — how sign-in methods combine.
- [Connect an IdP (SSO)](connect-idp.md) — the recommended primary path.
- [Offboarding & break-glass](offboarding.md) — removing access, and signing in when the directory is down.
