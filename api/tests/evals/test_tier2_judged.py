"""Tier 2: absolute scoring against a live model and a live judge.

Costs money, needs a provider key, and is in no CI gate.

    ASSISTANT_EVAL_API_KEY=… make eval-judged ARM=with-docs

The regression and reporting mode: scores tracked over time against thresholds.
To ask whether a *change* helped, use pairwise — more sensitive to a small real
difference than watching a mean wobble.
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
from tests.evals.metrics import load_cases

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

    scores = []
    async with docs_search_backend() as docs_search:
        for case in cases:
            result = await run_case(
                arm,
                case.question,
                gateway=EvalGateway(can_write=arm.workspace.get("can_write", False)),
                docs_search=docs_search,
                case_name=case.name,
            )
            scores.append(await score_absolute(case, result))

    summary = summarise_scores(scores)
    summary |= {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "arm": arm.name,
        "assistant_model": settings.assistant_model,
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
    assert summary["faithfulness"] >= MIN_FAITHFULNESS
    assert summary["relevancy"] >= MIN_RELEVANCY
