# Contributing

DuckHaven welcomes contributions. This page covers the conventions; for environment setup see
[Local development](development.md), and for the test suite see [Testing](testing.md).

## Workflow

1. Branch from `main` with a type prefix: `feat/`, `fix/`, `chore/`, `docs/`, `refactor/`, or `test/` followed by a
   short hyphenated slug (for example `feat/agent-capability-advertisement`).
2. Make focused changes, with tests for every behavior change.
3. Open a pull request against `main`.

## Commits

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) and are checked by commitlint:

```text
feat: add query cancellation
fix: resolve agent reconnect race
docs: update deployment guide
```

Keep the subject in the imperative mood, at most 72 characters, with no trailing period.

## Tests are required

Every feature or fix ships with tests — frontend (Vitest + React Testing Library + MSW), API (pytest), or agent
(pytest). See [Testing](testing.md). Before opening a PR, make sure the suite and hooks pass:

```bash
make test && pre-commit run --all-files
```

## Documentation

The docs site is built with Material for MkDocs and published to GitHub Pages by the
`Docs` workflow. Build and preview it locally with:

```bash
pip install 'mkdocs-material[imaging]==9.5.49'
mkdocs serve
```

The `[imaging]` extra (Pillow + CairoSVG) is required because the built-in `social` plugin
generates a per-page Open Graph / Twitter card image at build time; it also emits the
`og:*` and `twitter:*` meta tags. A few SEO touchpoints live outside the Markdown:

- `overrides/main.html` injects Schema.org JSON-LD (a site-wide `WebSite` entity and a
  `SoftwareApplication` entity on the homepage).
- `docs/robots.txt` allows AI answer/search bots, disallows AI training crawlers, and points
  to the sitemap. The directives are advisory — only well-behaved crawlers honor them.
- `docs/llms.txt` is a hand-curated index of the most useful pages for LLM crawlers. Keep it
  in sync when flagship pages are added, renamed, or removed.

## Releases

Maintainers cut releases by pushing a Git tag — see [Releasing](releasing.md).
