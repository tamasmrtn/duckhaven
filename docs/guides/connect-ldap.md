# Connect LDAP / AD

As a secondary federated path, DuckHaven can authenticate users against an LDAP directory or Active Directory. Users
sign in with their directory username/email and password on the normal login form; DuckHaven verifies the credentials
against the directory and provisions the account on first use. [OIDC SSO](connect-idp.md) is the recommended primary
path for most organizations.

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

DuckHaven binds as the service account, searches for the user, then **re-binds as that user** with the submitted
password to verify it. For Active Directory, a common filter is `(sAMAccountName={email})` or
`(userPrincipalName={email})`.

### Map groups to roles

DuckHaven reads the user's `memberOf` values and maps group DNs to
[global roles](../concepts/permissions.md#global-roles-permissions):

```bash
LDAP_GROUP_ROLE_MAP={"cn=dh-admins,ou=groups,dc=example,dc=com": "admin"}
```

Members of that group become `admin` on each sign-in; everyone else defaults to `user`.

## Operational notes

- **Always use TLS.** Prefer `ldaps://` or STARTTLS with a pinned CA (`LDAP_TLS_CA_CERT`); certificates are validated.
- **Use a read-only service account** for the search bind and rotate its password regularly.
- **Watch for lockouts.** Repeated failed user binds count against directory lockout policy. DuckHaven does not retry a
  failed bind.
- A bind timeout, a missing user, or a wrong password all result in a denied login (HTTP 401) — never a partial
  account.

## Related

- [Identity & permissions](../concepts/permissions.md) · [Connect an IdP (SSO)](connect-idp.md) ·
  [Offboarding & break-glass](../operations/offboarding.md)
