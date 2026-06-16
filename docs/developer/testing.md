# Testing

DuckHaven has a layered test architecture so each change can be verified at the cheapest level that proves it.

## Layers

| Layer | Scope | Frameworks |
|---|---|---|
| Unit | Pure functions, routers, services, agent executor | pytest + pytest-asyncio (api, agent), Vitest (web) |
| Component | React components and hooks, mocked transport | Vitest + React Testing Library + MSW |
| Cross-component | Live API + agent over the control channel | pytest |
| End-to-end | Full stack in Docker Compose | Playwright |

## Running tests

```bash
make test               # API + agent + web unit/component tests
make test-api           # API unit tests (coverage >= 80%)
make test-agent         # Agent unit tests (coverage >= 75%)
make test-web           # Web tests
make test-integration   # Integration (requires Postgres + Polaris)
make test-cross-component
make test-e2e           # Playwright
```

Heavier layers (integration, cross-component, e2e) are env-gated so the default `make test` stays fast.

## What to add

- New utility or pure function → unit test inputs, outputs, and edge cases.
- New component → render output and user interactions.
- New query/hook → `renderHook` plus an MSW handler override.
- Bug fix → a regression test that fails before the fix.

## Before you push

```bash
make test && pre-commit run --all-files
```

See [Local development](development.md) for environment setup and [Contributing](contributing.md) for conventions.
