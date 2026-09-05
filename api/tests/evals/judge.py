"""Tier 2: the judged layer — faithfulness, answer relevancy, and pairwise wins.

The only part of the harness that costs money. Everything a free check can catch
is caught in tier 1; a judge is reserved for the two things that need judgement.

The judge is pinned and every report records it. An unpinned judge silently
invalidates comparison against older runs: a score falling from 4.3 to 4.0 could
mean the assistant got worse or the judge changed, and nothing in the number
distinguishes them.
"""

from __future__ import annotations

import os
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


@lru_cache(maxsize=1)
def judge_model() -> Any:
    """The judge, constructed the same way the product constructs a model.

    Cached: a judged run makes 168 calls, and building a provider per call leaves
    that many connection pools open.

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
    faithfulness: float
    relevancy: float
    reason: str


def _context(case: Case, result: RunResult) -> str:
    """What the answer is entitled to say. See ``compare._context``."""
    from tests.evals.compare import _context as _pairwise_context

    return _pairwise_context(case, result)


async def score_absolute(case: Case, result: RunResult) -> CaseScore:
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
        faithfulness=float(faithful.score),
        relevancy=float(relevant.score),
        reason=faithful.reason,
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


def summarise_scores(scores: list[CaseScore]) -> dict:
    """Means overall and per slice, plus the negative cases that failed outright.

    One faithfulness score of 1 on a negative case fails the run regardless of
    the mean: that case is what this tier exists to catch, and an average is
    exactly the wrong way to look at it.
    """

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    by_category: dict[str, list[float]] = {}
    by_provenance: dict[str, list[float]] = {}
    for score in scores:
        by_category.setdefault(score.category, []).append(score.faithfulness)
        by_provenance.setdefault(score.provenance, []).append(score.faithfulness)

    confabulated = [s.case for s in scores if s.negative and s.faithfulness <= 1.0]
    return {
        "cases": len(scores),
        "faithfulness": mean([s.faithfulness for s in scores]),
        "relevancy": mean([s.relevancy for s in scores]),
        "faithfulness_by_category": {k: mean(v) for k, v in sorted(by_category.items())},
        "faithfulness_by_provenance": {k: mean(v) for k, v in sorted(by_provenance.items())},
        "confabulated_on_negative_cases": confabulated,
        "passed": (
            mean([s.faithfulness for s in scores]) >= MIN_FAITHFULNESS
            and mean([s.relevancy for s in scores]) >= MIN_RELEVANCY
            and not confabulated
        ),
    }
