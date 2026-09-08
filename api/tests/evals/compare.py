"""Pairwise comparison of two arms — the mode that answers "did my change help?".

More sensitive to a small real change than watching an absolute mean move by 0.1.

Every pair is judged twice with the answers swapped, and a win counts only when
the judge agrees both ways. Judges prefer whichever answer they see first by a
reported 10-15 points of win rate, larger than most effects worth measuring, so
a single-order verdict is not evidence. Disagreements become ties and are
counted as the *flip rate*.

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
from api.services.assistant.knowledge.loader import load_index, read_page
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
# Reliability work puts the typical rate near 14%.
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


# Shared across however many pages a case names, rather than capped per page: a
# flat cap truncates before the cited section and the judge then rules a correct
# citation a fabrication. A budget sends the common one-or-two-page case whole.
_CONTEXT_BUDGET = 24_000
_MIN_PER_PAGE = 2_000


def _pages_search_offered(already: list[str], results: tuple[RunResult, ...]) -> str:
    """Index entries for pages a search surfaced but nothing opened.

    A model can cite a page it only saw in search results — that is what search
    is for — and the judge then has to place the citation. Summaries rather than
    page text: this verifies that a page exists and what it covers, at a line
    each, without spending the budget the opened pages need.
    """
    index = load_index()
    seen = set(already)
    lines = []
    for result in results:
        for path in result.searched_paths:
            page = index.get(path)
            if path in seen or page is None:
                continue
            seen.add(path)
            lines.append(f"{path} — {page.title}: {page.summary}")
    return "\n".join(lines)


def _instructions_given(results: tuple[RunResult, ...]) -> str | None:
    """The product facts the assistant was handed before the turn began.

    The third thing an answer is entitled to rest on, after pages and tool
    results, and the last one the judge could not see. Governance answers refuse
    correctly and then explain *why* — writes need explicit approval, the
    account has limited grants, snapshots are never expired — every clause of it
    from BASE_PROMPT or PRODUCT_PROMPT. Against a context without them the
    faithfulness rubric's own override applies ("describes a feature absent from
    the context scores 1, however reasonable it sounds") and three correct
    refusals scored 1.

    Kept whole, page index included. Dropping it to save a third of the text was
    a mistake: *which pages exist* is exactly the assertion a citation has to be
    checked against, and without it the judge scored a correct pointer to
    guides/service-accounts.md — a real, indexed page — as an invented one.
    """
    text = next((r.instructions for r in results if r.instructions), "")
    return text.strip() or None


def _context(case: Case, *results: RunResult) -> str:
    """The evidence an answer is entitled to rest on: pages *and* tool results.

    Pages come from the case file rather than from what the arms happened to
    open, because the assistant answers most product questions from resident
    knowledge without opening anything, and an empty context makes every correct
    answer look invented.

    Tool results are here because leaving them out had the same effect on the
    other half of the case set. Asked to chart revenue, the assistant queried the
    curated metric and reported the figures it got back; the judge saw only
    `concepts/assistant.md`, which contains no revenue, and scored the answer 1
    for fabrication. A number the assistant looked up is not a number it made up,
    and faithfulness cannot tell the difference without seeing the lookup.
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

    # Never trim below the point where a page stops being usable evidence — and
    # so that the floor cannot quietly repeal the budget, drop the pages that do
    # not fit rather than shrinking every share below it.
    pages = pages[: _CONTEXT_BUDGET // _MIN_PER_PAGE]
    per_page = max(_MIN_PER_PAGE, _CONTEXT_BUDGET // len(pages)) if pages else 0
    blocks = []
    for path, page in pages:
        text = page["text"]
        if len(text) > per_page:
            text = text[:per_page] + f"\n[… {len(text) - per_page:,} characters not shown]"
        blocks.append(f"--- {path} ({page['title']}) ---\n{text}")

    seen: set[tuple[str, str]] = set()
    for result in results:
        for tool, summary in result.tool_results:
            if (tool, summary) in seen:
                continue
            seen.add((tool, summary))
            blocks.append(f"--- tool result: {tool} ---\n{summary}")

    if found := _pages_search_offered(paths, results):
        blocks.append(
            "--- pages search_docs returned, which the assistant saw without opening ---\n" + found
        )

    if instructions := _instructions_given(results):
        blocks.append(f"--- the assistant's standing instructions ---\n{instructions}")

    # Chosen by whether there are *pages*, not by whether there is anything at
    # all. Tool results are evidence of what a query returned, never of what the
    # product does — so a case with no documentation is still a case with no
    # documentation, and the judge needs telling. Keying this off the combined
    # blocks silently dropped the guidance the moment tool results were added,
    # and governance fell from 4.75 to 3.00 in one run.
    if not pages:
        blocks.append(_no_documentation_guidance(case))
    return "\n\n".join(blocks)


def _no_documentation_guidance(case: Case) -> str:
    """What "no page covers this" means, which depends on what was asked."""
    if case.category in ("product_knowledge", "unanswerable"):
        return (
            "--- no documentation covers this question ---\n"
            "That is itself informative. Beyond what the standing instructions above "
            "state, an answer that confidently describes a DuckHaven capability here is "
            "very likely inventing one, and an answer that says so is correct."
        )
    # Rubric-agnostic on purpose: this text reaches the faithfulness judge too,
    # which scores on a single 1-5 scale and has no numbered criteria to defer to.
    return (
        "--- no documentation covers this question ---\n"
        "It asks about the workspace's data rather than the product. Tool results above "
        "show what a query produced, not what DuckHaven is; the standing instructions "
        "show what the assistant was told the product does, and restating those "
        "faithfully is grounded, not invented. Claims neither supports are unverifiable "
        "here rather than fabricated — judge what can be judged and do not mark an "
        "answer down for what this context cannot settle either way."
    )


async def compare(arm_a: str, arm_b: str, docs_search=None) -> dict:
    a_config, b_config = ArmConfig.load(arm_a), ArmConfig.load(arm_b)
    model_a = a_config.model or settings.assistant_model
    model_b = b_config.model or settings.assistant_model
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
                # Without these a verdict can only be argued about, not checked.
                "answer_a": a.answer,
                "answer_b": b.answer,
                "tools_a": a.tools_called,
                "tools_b": b.tools_called,
                "pages_a": a.doc_paths,
                "pages_b": b.doc_paths,
            }
        )

    return _report(arm_a, arm_b, outcomes, model_a, model_b)


def _report(arm_a: str, arm_b: str, outcomes: list[dict], model_a: str, model_b: str) -> dict:
    counts = Counter(o["winner"] for o in outcomes)
    decided = counts["A"] + counts["B"]
    flips = sum(1 for o in outcomes if o["flipped"])
    # A plain tie cannot flip — the judge declined in both orders — so counting
    # it in the denominator hides position bias behind however many cases the
    # rubric could not separate. The comparable set is the pairs the judge
    # decided at all: the ones it called consistently, plus the ones it flipped.
    comparable = decided + flips
    by_category: dict[str, Counter] = {}
    for outcome in outcomes:
        by_category.setdefault(outcome["category"], Counter())[outcome["winner"]] += 1

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "arm_a": arm_a,
        "arm_b": arm_b,
        # A comparison against a run scored by a different judge is not a
        # comparison; recording it is what makes that checkable later.
        "judge_model": JUDGE_MODEL,
        "judge_temperature": JUDGE_SETTINGS.get("temperature"),
        # Per arm, and captured before the run: `_arm_settings` restores the
        # process default in its finally, so reading settings here recorded a
        # model that never ran — and a pairwise run has two of them anyway.
        "model_a": model_a,
        "model_b": model_b,
        "cases": len(outcomes),
        "wins_a": counts["A"],
        "wins_b": counts["B"],
        "ties": counts["tie"],
        # Over decided pairs only: a rate diluted by ties says more about how
        # often the judge could tell than about which arm is better.
        "win_rate_a": round(counts["A"] / decided, 3) if decided else None,
        "flip_rate": round(flips / comparable, 3) if comparable else None,
        "trustworthy": bool(comparable) and flips / comparable <= MAX_TRUSTWORTHY_FLIP_RATE,
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
