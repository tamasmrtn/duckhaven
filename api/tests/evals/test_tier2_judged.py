"""Tier 2: absolute scoring against a live model and a live judge.

Costs money, needs a provider key, and is in no CI gate.

    ASSISTANT_EVAL_API_KEY=… make eval-judged ARM=with-docs

The regression and reporting mode: scores tracked over time against thresholds.
To ask whether a *change* helped, use pairwise — more sensitive to a small real
difference than watching a mean wobble.

Each case is sampled ``SAMPLES_PER_CASE`` times, because the assistant's
temperature is not pinned and one sample makes the gate a coin flip on the cases
near it. The headline reliability number is **pass^k**: the share of cases whose
every sample scored well. Nobody resamples a production answer and keeps the
best, so "all k succeeded" is the deployment-relevant question — not "at least
one did", which is pass@k and rewards variance.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api.config import settings
from api.services.assistant.knowledge import generate
from api.services.assistant.knowledge.loader import load_index
from tests.evals.fixtures import EvalGateway
from tests.evals.harness import (
    SAMPLING_CONCURRENCY,
    ArmConfig,
    _arm_settings,
    docs_search_backend,
    retrying,
    run_case_once,
)
from tests.evals.judge import (
    JUDGE_MODEL,
    JUDGE_SETTINGS,
    MIN_FAITHFULNESS,
    MIN_PASS_K,
    MIN_RELEVANCY,
    SAMPLES_PER_CASE,
    score_absolute,
    summarise_scores,
)
from tests.evals.metrics import behaviour_scores, load_cases

REPORTS_DIR = Path(__file__).with_name("reports")

pytestmark = pytest.mark.skipif(
    not os.getenv("ASSISTANT_EVAL_API_KEY"),
    reason="ASSISTANT_EVAL_API_KEY not set; tier 2 calls a model and costs money",
)


@pytest.fixture(autouse=True)
def _real_models_allowed(monkeypatch):
    """The assistant suite blocks live model calls; this is the one place that
    opts out, explicitly and under an env gate."""
    from pydantic_ai import models

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    monkeypatch.setattr(settings, "assistant_docs_dir", generate._repo_root() / "docs")
    # What _build_model reads for an OpenAI-compatible endpoint. A hosted
    # provider takes its own standard variable from the environment instead.
    monkeypatch.setattr(settings, "assistant_api_key", os.environ["ASSISTANT_EVAL_API_KEY"])
    load_index.cache_clear()
    yield
    load_index.cache_clear()


async def test_absolute_scores_meet_their_thresholds():
    arm = ArmConfig.load(os.getenv("ASSISTANT_EVAL_ARM", "with-docs"))
    cases = load_cases()
    semaphore = asyncio.Semaphore(SAMPLING_CONCURRENCY)

    async def one(case, sample, docs_search):
        """One sample of one case: run it, then score it. Both retried."""
        async with semaphore:
            result = await retrying(
                lambda: run_case_once(
                    arm,
                    case.question,
                    gateway=EvalGateway(can_write=arm.workspace.get("can_write", False)),
                    docs_search=docs_search,
                    case_name=case.name,
                    sample=sample,
                )
            )
            score = await retrying(lambda: score_absolute(case, result, sample=sample))
        return case, result, score

    scores = []
    runs = []
    async with docs_search_backend() as docs_search:
        # One `_arm_settings` block around the whole fan-out. Every task here
        # patches the same arm, so there is nothing to race on; the guard is
        # against entering it per task, which would let whichever finishes
        # first restore settings the other tasks still need. A TaskGroup, not a
        # bare gather, so a failure cancels the rest before the block restores
        # anything.
        with _arm_settings(arm):
            tasks = []
            # Flattened so the semaphore bounds the whole run: a task per
            # (case, sample), not one long case at a time.
            async with asyncio.TaskGroup() as group:
                for case in cases:
                    for sample in range(SAMPLES_PER_CASE):
                        tasks.append(group.create_task(one(case, sample, docs_search)))
        # Order is fixed after the fan-out so the report stays comparable
        # between runs however the tasks interleaved.
        for case, result, score in sorted(
            (task.result() for task in tasks), key=lambda item: (item[0].name, item[2].sample)
        ):
            runs.append((case, result.answer, result.tools_called))
            scores.append(score)

    summary = summarise_scores(scores)
    # Free, deterministic, and computed from what the run already collected — so
    # they ride along here rather than needing a tier of their own.
    summary |= behaviour_scores(runs, set(load_index().paths))
    summary |= {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "arm": arm.name,
        # The arm's model, not the process default: `_arm_settings` has already
        # restored the latter by the time the report is built.
        "assistant_model": arm.model or settings.assistant_model,
        "judge_model": JUDGE_MODEL,
        "judge_temperature": JUDGE_SETTINGS.get("temperature"),
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = summary["generated_at"].replace(":", "").replace("-", "")
    (REPORTS_DIR / f"absolute-{arm.name}-{stamp}.json").write_text(json.dumps(summary, indent=2))
    print("\n" + json.dumps(summary, indent=2))

    # Reported before asserting, so a failing run still leaves a usable report.
    assert not summary["confabulated_on_negative_cases"], (
        "the assistant answered a negative case confidently, on at least one sample: "
        f"{summary['confabulated_on_negative_cases']}"
    )
    # A governance failure, not a rate to average: one forbidden call is a case
    # where the assistant reached past its grants and the judge would never see
    # it, because the answer that follows can read perfectly well.
    assert not summary["forbidden_tool_calls"], (
        f"forbidden tools were called on: {summary['forbidden_tool_calls']}"
    )
    assert summary["faithfulness"] >= MIN_FAITHFULNESS
    assert summary["relevancy"] >= MIN_RELEVANCY
    assert summary["pass_k"] >= MIN_PASS_K, (
        f"pass^{summary['k']} was {summary['pass_k']}: "
        f"{len(summary['failed_cases'])} cases failed a sample: {summary['failed_cases']}"
    )
    # tool_choice and refusal_rate_on_negative_cases are reported, not gated:
    # nothing has measured them yet, and a bar set from a guess either passes
    # meaninglessly or fails a run nobody can fix. Set them from the first run.
