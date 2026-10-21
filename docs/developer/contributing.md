# Contributing

DuckHaven welcomes contributions. This page covers the conventions; for environment setup see
[Local development](development.md), and for the test suite see [Testing](testing.md).

Everyone taking part follows the project's
[Code of Conduct](https://github.com/tamasmrtn/duckhaven/blob/main/CODE_OF_CONDUCT.md).

## Report a bug or ask a question

Open an [issue](https://github.com/tamasmrtn/duckhaven/issues/new/choose) and pick the form that fits: bug report,
feature request or question. The bug form asks for the version, which `GET /api/version` reports (see
[API versions](../reference/api-versions.md#check-the-version)), and for steps that reproduce the problem on a fresh
Compose stack. Problems with the CLI, the Python connector or the dbt and dlt adapters belong in
the [`duckhaven-clients`](https://github.com/tamasmrtn/duckhaven-clients/issues) repository.

!!! warning "Security vulnerabilities are not issues"
    Issues are public. Report a vulnerability privately through GitHub's
    [private vulnerability reporting](https://github.com/tamasmrtn/duckhaven/security/advisories/new) instead. The
    [security policy](https://github.com/tamasmrtn/duckhaven/blob/main/SECURITY.md) explains what counts as one and
    what happens after you report it.

## Workflow

1. Branch from `main` with a type prefix: `feat/`, `fix/`, `chore/`, `docs/`, `refactor/`, or `test/` followed by a
   short hyphenated slug (for example `feat/agent-capability-advertisement`).
2. Make focused changes, with tests for every behavior change.
3. Open a pull request against `main`. The pull request template asks for a summary, a test plan and a short
   checklist.

Before starting a large change, such as a new feature, a new dependency or a change to the wire format between the API
and agents, open an issue describing the problem and the approach. Agreeing on the design first saves rewriting a
finished pull request.

## Commits

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) and are checked by commitlint:

```text
feat: add query cancellation
fix: resolve agent reconnect race
docs: update deployment guide
```

Keep the subject in the imperative mood, at most 72 characters, with no trailing period.

## Sign off your commits

DuckHaven uses the [Developer Certificate of Origin](https://developercertificate.org/) (DCO) instead of a contributor
license agreement. By adding a `Signed-off-by` line to a commit, you certify that you wrote the change, or otherwise
have the right to submit it, under the project's license (AGPL-3.0-or-later). `git commit -s` adds the line from your
`user.name` and `user.email`:

```text
feat: add query cancellation

Signed-off-by: Jane Doe <jane@example.com>
```

The `DCO sign-off` CI job checks every commit in a pull request and fails when a commit has no sign-off whose email
matches its author. Merge commits and Dependabot commits are exempt. To sign off commits you already made, rebase and
push again:

```bash
git rebase --signoff origin/main
git push --force-with-lease
```

## AI-assisted contributions

You may use AI tools, such as code assistants and large language models, to prepare a change. The same rules apply
however the change was produced:

- **You are the author.** The person who opens the pull request is responsible for the whole change. Tools are not
  contributors, and the `Signed-off-by` line is yours.
- **Understand it end to end.** Be ready to explain and justify the design and every line of code during review.
- **Disclose significant help.** Say in the pull request description when an AI tool wrote a substantial part of the
  change, so reviewers know what to look for.
- **Licensing still applies.** Only submit generated code you have the right to contribute under AGPL-3.0-or-later.

## Tests are required

Every feature or fix ships with tests — frontend (Vitest + React Testing Library + MSW), API (pytest), or agent
(pytest). See [Testing](testing.md). Before opening a PR, make sure the suite and hooks pass:

```bash
make test && pre-commit run --all-files
```

## Documentation

The docs site is built with Material for MkDocs and published to GitHub Pages by the
`Docs` workflow. The toolchain is pinned in `requirements-docs.txt`. Preview the site locally with:

```bash
make docs-serve
```

Write pages to the types, templates and rules in [Writing docs](writing-docs.md). Before opening a pull request that
touches `docs/`, run `make docs-check`. It lints prose with [Vale](https://vale.sh/docs/install) (install the binary
once) and Markdown with markdownlint, runs the page-structure tests, and builds the site in strict mode.

The `[imaging]` extra (Pillow + CairoSVG) is required because the built-in `social` plugin
generates a per-page Open Graph / Twitter card image at build time; it also emits the
`og:*` and `twitter:*` meta tags. A few SEO touchpoints live outside the Markdown:

- `overrides/main.html` injects Schema.org JSON-LD (a site-wide `WebSite` entity and a
  `SoftwareApplication` entity on the homepage).
- `docs/robots.txt` allows AI answer/search bots, disallows AI training crawlers, and points
  to the sitemap. The directives are advisory — only well-behaved crawlers honor them.
- `docs/llms.txt` is the index of pages for LLM crawlers. It is generated, together with the assistant's page index,
  by `make docs-index`; pre-commit and CI fail when it is stale.

## Releases

Maintainers cut releases by pushing a Git tag — see [Releasing](releasing.md).
