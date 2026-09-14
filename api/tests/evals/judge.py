"""Tier 2: the judged layer — faithfulness, answer relevancy, and pairwise wins.

The only part of the harness that costs money. Everything a free check can catch
is caught in tier 1; a judge is reserved for the two things that need judgement.

The judge is pinned and every report records it. An unpinned judge silently
invalidates comparison against older runs: a score falling from 4.3 to 4.0 could
mean the assistant got worse or the judge changed, and nothing in the number
distinguishes them.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from tests.evals.harness import RunResult
from tests.evals.metrics import Case

# Pinned deliberately. Override only to compare judges, never casually: a
# comparison against a run scored by a different judge is not a comparison.
JUDGE_MODEL = os.getenv("ASSISTANT_EVAL_JUDGE_MODEL", "anthropic:claude-sonnet-5")

# Separate from the assistant's endpoint, because judging with the model you are
# scoring is a conflict of interest worth being able to avoid.
JUDGE_BASE_URL = os.getenv("ASSISTANT_EVAL_JUDGE_BASE_URL") or None

# Temperature 0: a judge that disagrees with itself adds variance to every number
# it produces, and the point is detecting small changes.
JUDGE_SETTINGS = ModelSettings(temperature=0.0, max_tokens=512)


FAITHFULNESS_RUBRIC = """\
You are scoring one answer produced by a data-platform assistant.

You will be given: the user's QUESTION, the CONTEXT the assistant retrieved
(documentation pages and tool results), and the assistant's ANSWER.

Score FAITHFULNESS from 1 to 5. Faithfulness measures only whether the ANSWER is
supported by the CONTEXT. It does not measure helpfulness, style, or whether you
personally believe the claim.

5 — Every factual claim in the answer is supported by the context. Where the
    context is silent, the answer says so rather than filling the gap.
4 — All substantive claims are supported; a minor detail is unsupported but
    harmless and does not change what the reader would do.
3 — Mostly supported, but at least one claim goes beyond the context in a way a
    reader could act on.
2 — A central claim is unsupported by the context, or contradicts it.
1 — The answer is largely invented, or asserts a product capability the context
    does not describe.

Two rules that override the scale:
- An answer that correctly says the documentation does not cover something, and
  does not then speculate, scores 5. Admitting ignorance is faithful.
- An answer that is fluent, specific and plausible but describes a feature absent
  from the context scores 1, however reasonable it sounds. This is the failure
  mode being tested.

Report the score as a number from 1 to 5."""


RELEVANCY_RUBRIC = """\
You are scoring one answer produced by a data-platform assistant.

You will be given the user's QUESTION and the assistant's ANSWER.

Score ANSWER RELEVANCY from 1 to 5 — whether the answer addresses what was
actually asked. Ignore whether the claims are true; another scorer handles that.

5 — Directly answers the question asked, at the right level of detail.
4 — Answers it, with padding or a digression that does not obscure the answer.
3 — Partially answers it, or answers a nearby question the user did not ask.
2 — Largely off-target; the user would have to ask again.
1 — Does not engage with the question.

Two rules that override the scale:
- A refusal or a statement of inability is fully relevant when it addresses this
  question — "DuckHaven does not expire snapshots" scores 5 for a question about
  retention policies. Do not penalise an answer for being negative.
- A clarifying question is fully relevant when the question was genuinely
  ambiguous, and scores 2 when it was not — stalling is not relevance.

Report the score as a number from 1 to 5."""


PAIRWISE_RUBRIC = """\
You are comparing two answers to the same question from a data-platform
assistant.

You will be given the QUESTION, the CONTEXT available, and two answers labelled
Answer 1 and Answer 2, in no meaningful order.

Choose the better answer on these criteria, in this priority order:
1. Correctness against the context — an answer that invents a product capability
   loses to one that says it does not know, always.
2. Whether it answers what was asked.
3. Whether it cites the pages it used.
4. Concision.

If neither is clearly better on criteria 1 and 2, reply "tie". Do not break a
genuine tie on style or length."""


# Set where a competent assistant sits, not where a perfect one would: a
# threshold nobody can meet gets deleted, not fixed.
MIN_FAITHFULNESS = 4.2
MIN_RELEVANCY = 4.0

# How many times each case is run in the absolute tier. The assistant's
# temperature is not pinned, so one sample per case makes the gate a coin flip
# on the cases that sit near it; k samples turn that into a measurement. Two
# captures most of the variance reduction at half the cost of three.
SAMPLES_PER_CASE = 2

# What one *sample* must clear for its case to count as passing. The run-level
# means are continuous; pass^k needs a binary, and this is the cut. 4 is the
# rubric's own line between "a claim the reader could act on" and a minor
# unsupported detail, so it is the scale's judgement rather than a chosen bar.
MIN_SAMPLE_FAITHFULNESS = 4.0
MIN_SAMPLE_RELEVANCY = 4.0

# The share of cases that must pass every sample. At 45 cases a single failure
# costs 0.022, so 0.9 tolerates four unlucky cases and still names a run whose
# answers are not reliably good.
MIN_PASS_K = 0.9


@lru_cache(maxsize=1)
def judge_model() -> Any:
    """The judge, constructed the same way the product constructs a model.

    Cached: a sampled judged run makes hundreds of calls, and building a
    provider per call leaves that many connection pools open.

    A bare string works for a hosted provider; an OpenAI-compatible endpoint
    needs a real client, and passing the string through would silently score
    against whatever the default provider happened to be.
    """
    if not JUDGE_BASE_URL:
        return JUDGE_MODEL

    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    # Strip only a leading "openai:" — the rest may itself contain a colon, as
    # every Ollama tag does ("glm-5.1:cloud").
    name = JUDGE_MODEL.removeprefix("openai:")
    return OpenAIChatModel(
        name,
        provider=OpenAIProvider(
            base_url=JUDGE_BASE_URL,
            api_key=os.getenv("ASSISTANT_EVAL_API_KEY") or "not-required",
        ),
    )


class PairwiseVerdict(BaseModel):
    winner: Literal["1", "2", "tie"]
    reason: str


class GradedVerdict(BaseModel):
    """A rubric score on the 1-5 scale the rubrics define.

    Scored through a plain agent rather than a library judge framed around
    pass/fail statements: a 0-1 score would make thresholds of 4.2 and 4.0
    unreachable, and every run would fail for a reason nothing in the output
    explains.
    """

    score: int = Field(ge=1, le=5, description="The rubric score, from 1 to 5.")
    reason: str = Field(description="One sentence justifying the score.")


@dataclass(frozen=True)
class CaseScore:
    case: str
    category: str
    provenance: str
    negative: bool
    # Whether the case names documentation the answer can be checked against.
    # Faithfulness is groundedness in retrieved context, so on a case with no
    # such context it is not a weak signal, it is the wrong question.
    grounded: bool
    faithfulness: float
    relevancy: float
    reason: str
    # What the assistant actually did. Kept so a failing run explains itself:
    # the aggregate names the case that failed and nothing else, and re-running
    # to find out is both slow and not guaranteed to reproduce.
    answer: str = ""
    tools_called: tuple[str, ...] = ()
    doc_paths: tuple[str, ...] = ()
    # Which repetition produced this score. The report keeps every sample, so a
    # case that failed one of two is checkable rather than a mystery.
    sample: int = 0

    def passes(self) -> bool:
        """Whether this one sample meets its own pass bar.

        Ungrounded cases are judged on relevancy alone: faithfulness is
        groundedness in retrieved context, and the harness already refuses to
        let a case with no context be failed for having none.
        """
        if self.relevancy < MIN_SAMPLE_RELEVANCY:
            return False
        return self.faithfulness >= MIN_SAMPLE_FAITHFULNESS if self.grounded else True


def _context(case: Case, result: RunResult) -> str:
    """What the answer is entitled to say. See ``compare._context``."""
    from tests.evals.compare import _context as _pairwise_context

    return _pairwise_context(case, result)


async def score_absolute(case: Case, result: RunResult, *, sample: int = 0) -> CaseScore:
    """Faithfulness and answer relevancy, scored separately.

    Two calls rather than one: a combined prompt lets a judge average them, and
    an answer that is faithful but off-topic should score 5 and 2, not 3.5 twice.
    """
    faithful = await _grade(
        FAITHFULNESS_RUBRIC,
        f"QUESTION: {case.question}\n\nCONTEXT:\n{_context(case, result)}\n\n"
        f"ANSWER:\n{result.answer}",
    )
    relevant = await _grade(
        RELEVANCY_RUBRIC,
        f"QUESTION: {case.question}\n\nANSWER:\n{result.answer}",
    )
    return CaseScore(
        case=case.name,
        category=case.category,
        provenance=case.provenance,
        negative=case.negative,
        grounded=bool(case.doc_sources),
        faithfulness=float(faithful.score),
        relevancy=float(relevant.score),
        reason=faithful.reason,
        answer=result.answer,
        tools_called=tuple(result.tools_called),
        doc_paths=tuple(result.doc_paths),
        sample=sample,
    )


@lru_cache(maxsize=1)
def _graded_agent() -> Agent:
    return Agent(judge_model(), output_type=GradedVerdict, model_settings=JUDGE_SETTINGS)


@lru_cache(maxsize=1)
def _pairwise_agent() -> Agent:
    return Agent(judge_model(), output_type=PairwiseVerdict, model_settings=JUDGE_SETTINGS)


def reset_judge() -> None:
    """Drop the cached judge. For tests that change the configured model."""
    for cached in (judge_model, _graded_agent, _pairwise_agent):
        cached.cache_clear()


async def _grade(rubric: str, body: str) -> GradedVerdict:
    return (await _graded_agent().run(f"{rubric}\n\n{body}")).output


async def judge_pair(question: str, context: str, first: str, second: str) -> PairwiseVerdict:
    """One pairwise comparison, in the order given. Call twice, swapped."""
    result = await _pairwise_agent().run(
        f"{PAIRWISE_RUBRIC}\n\n"
        f"QUESTION: {question}\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"Answer 1:\n{first}\n\n"
        f"Answer 2:\n{second}"
    )
    return result.output


def resolve_pair(shown_a_first: str, shown_b_first: str) -> tuple[str, bool]:
    """Combine both orderings into one verdict, and say whether the judge flipped.

    Both arguments name a *slot*, so the second is read inverted. A win requires
    agreement in both directions; disagreement is position bias showing itself,
    recorded as a tie and as a flip so it stays visible.
    """
    first = {"1": "A", "2": "B", "tie": "tie"}[shown_a_first]
    second = {"1": "B", "2": "A", "tie": "tie"}[shown_b_first]
    if first == second and first != "tie":
        return first, False
    flipped = first != second and "tie" not in (first, second)
    return "tie", flipped


def pass_hat_k(passes: list[bool], k: int) -> float:
    """The draw-without-replacement estimate that all k samples pass.

    ``C(c, k) / C(n, k)`` — the unbiased estimator from tau-bench
    (arXiv:2406.12045). With exactly k samples it is all-or-nothing, which is
    the deployment-relevant question: a user asks once and gets one answer.
    """
    n = len(passes)
    if n < k:
        raise ValueError(f"pass^{k} needs at least {k} samples, got {n}")
    return math.comb(sum(passes), k) / math.comb(n, k)


def summarise_scores(scores: list[CaseScore], *, k: int = SAMPLES_PER_CASE) -> dict:
    """Means per slice, pass^k per case, and the failures that name themselves.

    ``scores`` holds every sample of every case, not one row per case. Means
    are taken per case and then across cases, so a case sampled twice does not
    count twice. pass^k is computed per case over that case's samples and
    averaged across cases — the tau-bench macro structure.

    One faithfulness score of 1 on a negative case fails the run regardless of
    the mean: that case is what this tier exists to catch, and an average is
    exactly the wrong way to look at it. With sampling, "one sample
    confabulated" is the same failure, and averaging across samples would hide
    it. The check spans every negative case — most of them name no
    documentation, and they are the ones most likely to invent a feature.

    The faithfulness *mean* is narrower. It covers only the cases that name
    documentation, because faithfulness measures whether an answer is supported
    by retrieved context and 26 of the 45 cases retrieve none: they ask whether
    the assistant routed to the semantic layer, refused a write, or resisted an
    injection. Scoring those on groundedness averaged a real signal together with
    a meaningless one. They are gated on the behaviour metrics, relevancy, and
    their pass^k instead, and their faithfulness is still reported, per case, as
    a diagnostic.
    """

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    grouped: dict[str, list[CaseScore]] = defaultdict(list)
    for score in scores:
        grouped[score.case].append(score)

    def case_mean(samples: list[CaseScore], field: str) -> float:
        return sum(getattr(s, field) for s in samples) / len(samples)

    by_category: dict[str, list[float]] = defaultdict(list)
    by_provenance: dict[str, list[float]] = defaultdict(list)
    for samples in grouped.values():
        by_category[samples[0].category].append(case_mean(samples, "faithfulness"))
        by_provenance[samples[0].provenance].append(case_mean(samples, "faithfulness"))

    grounded = [s for s in grouped.values() if s[0].grounded]
    behavioural = [s for s in grouped.values() if not s[0].grounded]
    confabulated = sorted(
        {
            score.case
            for samples in grouped.values()
            for score in samples
            if score.negative and score.faithfulness <= 1.0
        }
    )
    case_passes = {
        case: pass_hat_k([s.passes() for s in samples], k) for case, samples in grouped.items()
    }
    failures = sorted(case for case, rate in case_passes.items() if rate < 1.0)
    pass_k = mean(list(case_passes.values()))
    faithful_mean = mean([case_mean(s, "faithfulness") for s in grounded])
    relevancy_mean = mean([case_mean(s, "relevancy") for s in grouped.values()])
    return {
        "outcomes": [
            {
                "case": s.case,
                "category": s.category,
                "provenance": s.provenance,
                "negative": s.negative,
                "sample": s.sample,
                "faithfulness": s.faithfulness,
                "relevancy": s.relevancy,
                "passed": s.passes(),
                "reason": s.reason,
                "tools_called": list(s.tools_called),
                "doc_paths": list(s.doc_paths),
                "answer": s.answer,
            }
            for s in scores
        ],
        "cases": len(grouped),
        # Over the grounded cases only, case by case rather than sample by
        # sample. `faithfulness_cases` travels with it so a mean over nineteen
        # is never read as a mean over forty-five.
        "faithfulness": faithful_mean,
        "faithfulness_cases": len(grounded),
        # Reported, never gated: there is no retrieved context on these to be
        # faithful to. A low number here is a prompt to go and read the answers.
        "faithfulness_ungrounded": mean([case_mean(s, "faithfulness") for s in behavioural]),
        "faithfulness_ungrounded_cases": len(behavioural),
        "relevancy": relevancy_mean,
        "faithfulness_by_category": {k_: mean(v) for k_, v in sorted(by_category.items())},
        "faithfulness_by_provenance": {k_: mean(v) for k_, v in sorted(by_provenance.items())},
        # pass^k: the share of cases whose every sample passed. `failed_cases`
        # names the rest, because "a case failed at 1.0" is a capability gap or
        # bad luck, and re-running is how the difference is measured.
        "k": k,
        "pass_k": pass_k,
        "pass_k_cases": len(grouped),
        "failed_cases": failures,
        "confabulated_on_negative_cases": confabulated,
        "passed": (
            faithful_mean >= MIN_FAITHFULNESS
            and relevancy_mean >= MIN_RELEVANCY
            and pass_k >= MIN_PASS_K
            and not confabulated
        ),
    }
