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

| Tier | What it scores | Trigger | Model calls | Cost per run |
|---|---|---|---|---|
| 1a | Metric correctness, case-set validity, arm configuration, judge arithmetic | `make test-api` | none | free |
| 1b | Retrieval: recall@5 and MRR over the docs corpus | `make test-integration-api` (needs Postgres) | none | free |
| 2 absolute | Faithfulness and answer relevancy against thresholds | `make eval-judged` | 30 runs + 60 judge calls | **~$3.40** |
| 2 pairwise | Which of two arms is better | `make eval-compare` | 60 runs + 60 judge calls | **~$6.40** |

Tiers 1a and 1b need no provider key and run on every pull request. Tier 2 runs on demand only — there is no cron —
so it costs nothing when idle. Figures assume Claude Sonnet at \$3/M input and \$15/M output, and roughly four model
requests per case at 6k input and 400 output tokens each; check them against your own provider before enabling
anything on a schedule. A weekly absolute run would be about \$15/month.

**The judge is pinned and its identity is recorded in every report.** An unpinned judge silently invalidates
comparison against older runs: a faithfulness score that drops from 4.3 to 4.0 could mean the assistant got worse or
the judge changed, and nothing in the number tells you which.

**Pairwise comparisons are judged in both orders**, and a win counts only when the judge agrees with itself after the
answers are swapped. Judges systematically prefer whichever answer they see first — by a reported 10–15 points of win
rate, which is larger than most effects worth measuring. Disagreements become ties and are counted as the *flip rate*;
a run whose flip rate exceeds 25% is reported as inconclusive however decisive its headline looks.

A single faithfulness score of 1 on a negative case fails the run outright, regardless of the mean. That one case is
what the tier exists to catch, and an average is exactly the wrong way to look at it.

An **arm** is a named configuration of the assistant — which model, whether product knowledge is on, what the
workspace has — defined in `api/tests/evals/arms.yaml`. Arms configure the real `build_instructions` and
`build_toolset` rather than reimplementing them, so an arm can only describe a state the product can actually be in,
and a harness cannot drift into measuring something the assistant does not do.

```bash
make test-api                 # tier 1a, no key needed
make test-integration-api     # tier 1b, needs DATABASE_URL

export ASSISTANT_EVAL_API_KEY=…                             # tier 2 only, costs money
make eval-judged  ARM=with-docs                             # score one arm
make eval-compare ARM_A=with-docs ARM_B=baseline            # compare two
make eval-synth   ARGS="--limit 5"                          # draft candidate cases
```

Reports land in `api/tests/evals/reports/` (gitignored — a run's numbers belong in the pull request that cites
them, not in the tree). The GitHub workflow `assistant-eval.yml` runs the same targets on `workflow_dispatch`; it
ships inert and exits with a notice until someone adds `ASSISTANT_EVAL_API_KEY` to repository secrets. Neither
`workflow_dispatch` nor `schedule` is reachable from a fork, which is what makes a secret safe there — running this
on `pull_request` would not be.

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
