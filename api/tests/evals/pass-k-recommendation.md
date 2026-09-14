# Recommendation: adopt pass^k sampling in the judged tier

Status: implemented (September 2026) as k = 2 samples per case, gating on
pass^2 ≥ 0.9. This document is kept as the rationale; the harness, not this
file, is the source of truth.

Written after measuring answer variance across the seven `absolute-with-docs`
reports (Sept 2026).

## The problem this addresses

The judged tier currently takes **one sample per case** (`run_case`), scores it,
and gates on means — faithfulness ≥ 4.2, relevancy ≥ 4.0 — plus hard gates (no
confabulated negative case, no forbidden tool call). But the assistant's
temperature is not pinned (only `max_tokens` is set in `agent.py`), so answers
vary between runs. The variance is not hypothetical:

- 27 of 42 cases changed faithfulness score across seven runs of the *same*
  arm, model and judge. `describe_not_information_schema` scored
  1, 1, 5, 5, 5, 5; `no_chart_generation` scored 1, 1, 3, 4, 4, 5.
- The run-level faithfulness mean ranged **4.26 → 4.90** against a **4.2**
  gate. Today the gate passes or fails on sampling luck.

## The recommendation

Adopt **pass^k**, not pass@k, as the reliability metric, with k = 2 or 3
samples per case in the judged tier:

- **pass^k** = "this case produced a good answer on all k samples". This is the
  deployment-relevant quantity — a user asks a question once and gets one
  answer; nobody resamples and keeps the best. It also matches the harness's
  existing gating philosophy, which is already all-or-nothing: one confabulated
  negative case fails the run, one forbidden tool call is named rather than
  averaged away. pass^k is that stance applied per-case.
- Do **not** adopt pass@k as a headline metric. It measures a ceiling the
  product does not offer and rewards high-variance behaviour: a model that is
  occasionally brilliant and often mediocre scores well, which is the opposite
  of what the gate exists to catch. Keep it, at most, as a triage diagnostic —
  when a case fails, re-run it k times to answer "can this arm ever pass this
  case?", which is cheap because it only triggers on failures.

### What to report (and what to gate on)

- Per-case outcome distributions alongside the 1–5 judge scores, rather than
  forcing every sample through a binary pass/fail before aggregation. The
  harness deliberately moved away from binary framing once (see the
  `GradedVerdict` docstring); choosing "pass = faithfulness ≥ 4" would
  reintroduce an arbitrary cut and count a 4/5 split as total failure.
- Gate on the *mean over samples* (now much lower-variance) and keep the hard
  gates, expressed as "all k samples refused" / "no sample called a forbidden
  tool".
- Expected effect: k = 3 cuts the sampling variance of the run mean by roughly
  a third and turns "a case failed at 1.0" from a mystery into a measurement
  (capability gap vs. bad luck).

### Known costs and caveats

- **Cost**: a judged run is ~168 sequential judge calls over ~30 minutes; k = 3
  triples assistant runs and up to triples judge calls. k = 2 captures most of
  the statistical benefit at half the cost.
- **Binarisation pressure**: pass^k needs a pass/fail definition to be computed
  at all; resist letting it replace the 1–5 scores in reporting.
- **Small-k brittleness**: a case with a per-sample pass rate near 0.5 looks
  like a coin flip at k = 3. Report per-case rates, not just the aggregate.
- **Sample correlation**: retrieval and corpus state are shared across samples,
  so samples are not fully independent; treat pass^k empirically (measured over
  samples) rather than deriving it from pass@1.
- **Zero-cost alternative, rejected as the primary fix**: pinning the assistant
  temperature would remove much of the variance — but production does not pin
  it, and an eval should measure the behaviour users experience. Sampling more,
  not sanitising the sampler, is the honest fix. (If production ever pins
  temperature, the arms should mirror that.)

## Prerequisite: parallelise the harness

The harness used to be strictly sequential: `run_case` was awaited one case at a
time in `compare()` and in `test_tier2_judged.py`. Multiplying samples by k
while keeping the serial loop would turn a half-hour judged run into a
multi-hour one, which is how eval suites stop getting run. What shipped:

- The absolute tier gathers one task per (case, sample), bounded by a
  `SAMPLING_CONCURRENCY = 4` semaphore, with `retrying()` around both the
  assistant run and the judge calls. A case's samples are interleaved with
  every other case's rather than run one case at a time.
- **Parallelise within an arm, not across arms.** `_arm_settings` patches
  process-global settings (`settings.assistant_model`,
  `assistant_openai_base_url`, `assistant_docs_enabled`) and restores them in a
  `finally`. Entering that context manager per concurrent call is unsafe: the
  first task to finish would restore the process defaults while the others were
  still mid-run. The fan-out therefore hoists a single `_arm_settings` block
  around the whole gather and calls `run_case_once`, which assumes the settings
  are already applied. Keep arm-level execution sequential.
- The pairwise tier still runs arms sequentially and is unchanged.
- The report keeps per-sample scores, so historical reports remain comparable
  apart from the new keys.
