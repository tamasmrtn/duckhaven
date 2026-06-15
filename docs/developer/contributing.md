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

## Releases

Maintainers cut releases by pushing a Git tag — see [Releasing](releasing.md).
