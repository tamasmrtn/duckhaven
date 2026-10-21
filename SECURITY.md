# Security Policy

**Please do not report security vulnerabilities through public GitHub issues, pull requests or discussions.**

## Reporting a vulnerability

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/tamasmrtn/duckhaven/security/advisories/new): open the
repository's **Security** tab and choose **Report a vulnerability**. Only the maintainers can see the report.

Please include:

- What the vulnerability lets an attacker do, and what access they need first (anonymous, workspace member, workspace
  admin, an agent token, ...).
- Steps to reproduce, ideally against the bundled Docker Compose stack.
- The version you tested: `curl -s http://<host>/api/version` reports it, or name the commit for a build from `main`.
- Any logs, requests or proof-of-concept code that show the problem.

## What to expect

DuckHaven is maintained by volunteers, so these are targets rather than guarantees:

- We acknowledge a report within 5 business days.
- We confirm or reject it, with our reasoning, after we have reproduced it.
- We fix confirmed issues in a new release, then publish a GitHub security advisory and request a CVE where one is
  warranted. We credit you in the advisory unless you ask us not to.
- We ask that you keep the details private until the fix is released. We coordinate the disclosure date with you.

## Supported versions

DuckHaven is pre-1.0. Security fixes land on `main` and ship in the next release; earlier releases are not patched.

| Version | Supported |
|---|---|
| Latest release | Yes |
| `main` (the `:latest` images) | Yes |
| Older releases | No |

## Security model

Read this before reporting: it defines what DuckHaven protects and what it relies on others for.

DuckHaven runs SQL from its users on DuckDB. DuckDB itself treats SQL as trusted code, so a query that reads local
files, opens network connections or loads extensions is not a vulnerability in DuckDB
([DuckDB's security policy](https://github.com/duckdb/duckdb/security/policy)). DuckHaven is the layer that makes that
SQL safe to accept from users: it authorizes every request at the API, filters each statement against an allowlist,
and runs agents in a sandbox. Bypassing any of those layers **is** a DuckHaven vulnerability, even when the underlying
behavior is DuckDB working as designed.

### In scope

- Bypassing authentication, or acting as another user or service account.
- Reaching a workspace you are not a member of, or a catalog, schema or table that a scoped grant denies you.
- Getting a statement past the SQL allowlist that it should refuse, such as `ATTACH`, `COPY`, `INSTALL`/`LOAD`, `SET`
  or a function that reads files or opens another database. In a SQL session, this includes anything outside the
  session's statement policy or staging prefix.
- Escaping an agent's sandbox: widening its DuckDB configuration after it is locked, writing outside its results volume
  and `/tmp`, or reaching the network on a stack that has not applied the egress opt-out.
- The control plane executing user SQL, or opening storage, on a user's behalf.
- Leaking storage credentials, tokens, passwords or vended secrets to someone who should not see them, including
  through logs, error messages, query history or the AI assistant.
- Cross-site scripting, request forgery or similar flaws in the web UI.
- Running work on an agent, or restarting or administering it, without the agent access tier that allows it.
- An agent, or anyone holding an agent's credential, reaching data or controls beyond what that agent is given.

### Not in scope

- Anything a user can already do with their own grants, such as reading a table they have access to.
- Behavior on a stack where a protection was deliberately turned off, such as `SANDBOX_LOCK_CONFIGURATION=false` or the
  egress opt-out.
- Compromise of the infrastructure DuckHaven runs on or trusts: the host, Docker, Postgres, Polaris, the object store
  or your identity provider. A stolen admin password or bootstrap token is in this category.
- Denial of service through expensive queries or an undersized deployment.
- Vulnerabilities in a dependency that DuckHaven does not expose. Report those upstream; we track dependency CVEs with
  Dependabot and Trivy.
- Findings from automated scanners without a demonstrated impact on DuckHaven.

When a report is a real bug but not a security issue, we will ask you to open a regular issue for it.
