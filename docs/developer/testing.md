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

### Elastic compute in the cross-component layer

Scale-out can only be observed where compute is *absent*, so the elastic tests cannot share the main
cross-component stack — its agent is already connected and would simply serve every request. They
boot a second API against a sibling database (`<your DB>_elastic`, dropped and recreated per run, so
the `DATABASE_URL` role needs `CREATE DATABASE`) with no agent of its own.

The compute backend there is `null`: it records an instance id and creates nothing, so the test plays
the part the cloud plays in production — it reads the bootstrap token the control plane minted for
the pre-created agent row and starts a real agent process with it. Everything on the control-plane
side of that seam is the production path, including the WebSocket registration that revives the row
and binds the work parked for it.

## What to add

- New utility or pure function → unit test inputs, outputs, and edge cases.
- New component → render output and user interactions.
- New query/hook → `renderHook` plus an MSW handler override.
- Bug fix → a regression test that fails before the fix.

## Evaluating the AI assistant

The assistant is scored by a standing harness rather than by reading transcripts, because "did that prompt edit make
it better or worse?" is not a question anyone can answer by eye. The case set lives in `api/tests/evals/cases.yaml`
and is reviewed like code.

Every case records **where its answer lives** and **who wrote it**. The first makes retrieval scoring plain
arithmetic — recall@5 and MRR need no model at all. The second matters more than it looks: a question synthesised
*from* a page is trivially answerable by a system that retrieved that page, so scores are always reported split by
provenance. Mixing them flatters the system, measurably — auto-synthesised cases currently score recall@5 of 0.83
against hand-written cases' 0.75.

**Roughly a third of the set are negative cases** — questions whose correct answer is a refusal, an admission of
ignorance, or a clarifying question. They guard the sharpest risk in giving an assistant product knowledge: one that
has read the documentation and now confabulates fluently about features DuckHaven does not have. On those cases more
knowledge producing a more confident answer is the failure being tested.

| Tier | What it scores | Trigger | Cost |
|---|---|---|---|
| 1a | Metric correctness, case-set validity, arm configuration | `make test-api` | free |
| 1b | Retrieval: recall@5, MRR over the docs corpus | `make test-integration-api` (needs Postgres) | free |
| 2 | Faithfulness and answer relevancy, judged | on demand only | ~$7 per run |

Tiers 1a and 1b need no provider key and run on every pull request. Tier 2 is not yet built; see
`docs/developer/assistant-knowledge-plan.md`.

An **arm** is a named configuration of the assistant — which model, whether product knowledge is on, what the
workspace has — defined in `api/tests/evals/arms.yaml`. Arms configure the real `build_instructions` and
`build_toolset` rather than reimplementing them, so an arm can only describe a state the product can actually be in,
and a harness cannot drift into measuring something the assistant does not do.

```bash
make test-api                 # tier 1a, no key needed
make test-integration-api     # tier 1b, needs DATABASE_URL
ASSISTANT_EVAL_API_KEY=… make eval-synth ARGS="--limit 5"   # draft candidate cases
```

`eval-synth` writes `cases.candidate.yaml` for a human to promote by hand. It never adds to the golden set itself,
and it never drafts a negative case — asserting that something does *not* exist requires knowing the whole product,
which a model shown one page can only invent.

Grow the set from real failures rather than by enumerating questions at a desk. `search_docs` records `no_results` on
its audit row precisely so the questions the documentation failed to answer can be found later.

## Before you push

```bash
make test && pre-commit run --all-files
```

See [Local development](development.md) for environment setup and [Contributing](contributing.md) for conventions.
