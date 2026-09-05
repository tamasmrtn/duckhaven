"""Pairwise comparison of two arms — the mode that answers "did my change help?".

Absolute scores drift and wobble; a win rate over the same cases is more
sensitive to a small real change, which is why this is the mode for judging a
prompt edit or a model swap rather than watching a mean move by 0.1.

Every pair is judged **twice, with the answers swapped**, and a win counts only
when the judge agrees with itself both ways. Judges systematically prefer
whichever answer they see first — a reported 10–15 points of win rate, larger
than most effects worth measuring — so a single-order verdict is not evidence.
Disagreements become ties and are counted separately as the *flip rate*: a run
with a high flip rate is one whose verdicts should not be trusted, however
decisive its headline looks.

    make eval-compare ARM_A=with-docs ARM_B=baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from api.config import settings
from api.services.assistant.knowledge.loader import read_page
from tests.evals.fixtures import EvalGateway
from tests.evals.harness import (
    ArmConfig,
    RunResult,
    docs_search_backend,
    retrying,
    run_case,
)
from tests.evals.judge import JUDGE_MODEL, JUDGE_SETTINGS, judge_pair, resolve_pair
from tests.evals.metrics import Case, load_cases

REPORTS_DIR = Path(__file__).with_name("reports")

# Above this, position bias is dominating and the comparison is not evidence.
# Reliability work puts the typical rate around 14%; well past that means the
# rubric is not discriminating between these two arms.
MAX_TRUSTWORTHY_FLIP_RATE = 0.25


async def _answer(arm: ArmConfig, case: Case, docs_search) -> RunResult:
    return await retrying(
        lambda: run_case(
            arm,
            case.question,
            gateway=EvalGateway(can_write=arm.workspace.get("can_write", False)),
            docs_search=docs_search,
            case_name=case.name,
        )
    )


# Total characters of documentation the judge sees, shared across however many
# pages a case names rather than capped per page.
#
# A flat per-page cap of 2,500 quietly cost two correct answers. The assistant
# read reference/sql-support.md in full - 9,258 characters, well inside
# read_doc_page's own limit - and cited a real section beginning at character
# 4,683. The judge saw the first 2,500 characters, could not find the section,
# and called a correct citation a fabrication. Both losses in the run that
# exposed this were of that shape.
#
# A budget rather than a per-page cap means the common case, one or two pages,
# is sent whole; only a case naming many pages is trimmed, and then evenly.
_CONTEXT_BUDGET = 24_000
_MIN_PER_PAGE = 2_000


def _context(case: Case, *results: RunResult) -> str:
    """The ground truth for this question, as page text rather than page names.

    The first version listed only the paths the arms had *opened*, which was
    wrong in a way that inverted the whole result: the assistant answers most
    product questions straight from its resident knowledge without opening
    anything, so the judge saw an empty context and — following criterion 1 —
    marked every correct, documented answer as an invented capability. The arm
    that knew things was punished for knowing them.

    The case file already names where each answer lives. Feeding that text to the
    judge is what makes "supported by the context" a real test instead of a test
    of whether a tool happened to fire.
    """
    paths: list[str] = []
    for source in (*case.doc_sources, *(p for r in results for p in r.doc_paths)):
        if source not in paths:
            paths.append(source)

    pages = []
    for path in paths:
        try:
            pages.append((path, read_page(path)))
        except Exception:  # noqa: BLE001 — a missing page is context we lack, not a failure
            continue

    # Share the budget, and never trim a page below the point where it stops
    # being usable evidence.
    per_page = max(_MIN_PER_PAGE, _CONTEXT_BUDGET // len(pages)) if pages else 0
    blocks = []
    for path, page in pages:
        text = page["text"]
        if len(text) > per_page:
            text = text[:per_page] + f"\n[… {len(text) - per_page:,} characters not shown]"
        blocks.append(f"--- {path} ({page['title']}) ---\n{text}")
    if blocks:
        return "\n\n".join(blocks)

    # No page covers this. What that means depends on what was asked, and getting
    # it wrong in either direction costs the run its point.
    if case.category in ("product_knowledge", "unanswerable"):
        return (
            "No documentation page covers this question. That is itself informative: an "
            "answer that confidently describes a DuckHaven capability here is very likely "
            "inventing one, and an answer that says so is correct."
        )
    return (
        "This question is about the workspace's data rather than the product, so no "
        "documentation applies. Judge on criteria 2-4 and do not treat a factual answer "
        "as an invented capability."
    )


async def compare(arm_a: str, arm_b: str, docs_search=None) -> dict:
    a_config, b_config = ArmConfig.load(arm_a), ArmConfig.load(arm_b)
    cases = load_cases()
    outcomes: list[dict] = []

    for n, case in enumerate(cases, 1):
        print(f"  [{n}/{len(cases)}] {case.name}", flush=True)
        a = await _answer(a_config, case, docs_search)
        b = await _answer(b_config, case, docs_search)
        context = _context(case, a, b)
        # Both orders. The judge never learns which arm is which.
        first = await retrying(lambda: judge_pair(case.question, context, a.answer, b.answer))
        second = await retrying(lambda: judge_pair(case.question, context, b.answer, a.answer))
        winner, flipped = resolve_pair(first.winner, second.winner)
        outcomes.append(
            {
                "case": case.name,
                "category": case.category,
                "provenance": case.provenance,
                "negative": case.negative,
                "winner": winner,
                "flipped": flipped,
                "reason": first.reason,
                # The answers themselves, and what each arm did to produce them.
                # Without these a verdict can only be argued about: diagnosing the
                # first regression this harness found meant inferring the answers
                # from the judge's prose, which is guesswork dressed as analysis.
                "answer_a": a.answer,
                "answer_b": b.answer,
                "tools_a": a.tools_called,
                "tools_b": b.tools_called,
                "pages_a": a.doc_paths,
                "pages_b": b.doc_paths,
            }
        )

    return _report(arm_a, arm_b, outcomes)


def _report(arm_a: str, arm_b: str, outcomes: list[dict]) -> dict:
    counts = Counter(o["winner"] for o in outcomes)
    decided = counts["A"] + counts["B"]
    flips = sum(1 for o in outcomes if o["flipped"])
    by_category: dict[str, Counter] = {}
    for outcome in outcomes:
        by_category.setdefault(outcome["category"], Counter())[outcome["winner"]] += 1

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "arm_a": arm_a,
        "arm_b": arm_b,
        # Recorded, not assumed: a comparison against a run scored by a different
        # judge is not a comparison, and this is what makes that checkable later.
        "judge_model": JUDGE_MODEL,
        "judge_temperature": JUDGE_SETTINGS.get("temperature"),
        "assistant_model": settings.assistant_model,
        "cases": len(outcomes),
        "wins_a": counts["A"],
        "wins_b": counts["B"],
        "ties": counts["tie"],
        # Over decided pairs only. A win rate diluted by ties says less about
        # which arm is better than about how often the judge could tell.
        "win_rate_a": round(counts["A"] / decided, 3) if decided else None,
        "flip_rate": round(flips / len(outcomes), 3) if outcomes else 0.0,
        "trustworthy": bool(outcomes) and flips / len(outcomes) <= MAX_TRUSTWORTHY_FLIP_RATE,
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
        "outcomes": outcomes,
    }


def render(report: dict) -> str:
    lines = [
        f"{report['arm_a']} vs {report['arm_b']}  ({report['cases']} cases)",
        f"  judge:      {report['judge_model']} @ temperature {report['judge_temperature']}",
        f"  wins:       {report['arm_a']} {report['wins_a']} | "
        f"{report['arm_b']} {report['wins_b']} | ties {report['ties']}",
        f"  win rate:   {report['win_rate_a']} (decided pairs only)",
        f"  flip rate:  {report['flip_rate']}"
        f"{'' if report['trustworthy'] else '  ← too high; treat this run as inconclusive'}",
        "  by category:",
    ]
    lines += [f"    {name}: {dict(counts)}" for name, counts in report["by_category"].items()]
    return "\n".join(lines)


async def _run(arm_a: str, arm_b: str) -> dict:
    async with docs_search_backend() as docs_search:
        return await compare(arm_a, arm_b, docs_search=docs_search)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True, help="arm name from arms.yaml")
    parser.add_argument("--b", required=True, help="arm name from arms.yaml")
    args = parser.parse_args()

    report = asyncio.run(_run(args.a, args.b))
    print(render(report))

    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = report["generated_at"].replace(":", "").replace("-", "")
    path = REPORTS_DIR / f"compare-{args.a}-vs-{args.b}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"\nreport: {path}")


if __name__ == "__main__":
    main()
