"""Tier 2: absolute scoring against a live model and a live judge.

Costs money and needs a provider key, so it is skipped unless
``ASSISTANT_EVAL_API_KEY`` is set. It is not part of any CI gate — see
``docs/developer/testing.md`` for what it costs and when to run it.

    ASSISTANT_EVAL_API_KEY=… make eval-judged ARM=with-docs

This is the regression and reporting mode: scores tracked over time against
thresholds. To ask whether a *change* helped, use pairwise instead — it is more
sensitive to a small real difference than watching a mean wobble.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api.config import settings
from api.services.assistant.knowledge import generate
from api.services.assistant.knowledge.loader import load_index
from tests.evals.fixtures import EvalGateway
from tests.evals.harness import ArmConfig, docs_search_backend, run_case
from tests.evals.judge import (
    JUDGE_MODEL,
    JUDGE_SETTINGS,
    MIN_FAITHFULNESS,
    MIN_RELEVANCY,
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
    """The assistant suite blocks live model calls by default; this is the one
    place that must opt out, and it does so explicitly and under an env gate."""
    from pydantic_ai import models

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    monkeypatch.setattr(settings, "assistant_docs_dir", generate._repo_root() / "docs")
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ["ASSISTANT_EVAL_API_KEY"])
    load_index.cache_clear()
    yield
    load_index.cache_clear()


async def test_absolute_scores_meet_their_thresholds():
    arm = ArmConfig.load(os.getenv("ASSISTANT_EVAL_ARM", "with-docs"))
    cases = load_cases()

    scores = []
    runs = []
    async with docs_search_backend() as docs_search:
        for case in cases:
            result = await run_case(
                arm,
                case.question,
                gateway=EvalGateway(can_write=arm.workspace.get("can_write", False)),
                docs_search=docs_search,
                case_name=case.name,
            )
            runs.append((case, result.answer, result.tools_called))
            scores.append(await score_absolute(case, result))

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
        "the assistant answered a negative case confidently: "
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
    # tool_choice and refusal_rate_on_negative_cases are reported, not gated:
    # nothing has measured them yet, and a bar set from a guess either passes
    # meaninglessly or fails a run nobody can fix. Set them from the first run.
