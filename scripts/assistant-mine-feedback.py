#!/usr/bin/env python
"""Find the questions the assistant answered badly, from what it already records.

Read-only. Emits candidate eval cases for a human to triage into
``api/tests/evals/cases.yaml``. Nothing is promoted automatically, and nothing is
written to the database.

    DATABASE_URL=... ./scripts/assistant-mine-feedback.py --days 30

**This is an operator-run script, not an automated loop, and that is deliberate.**
Nothing in the schema records whether a user was satisfied — there is no
thumbs-up, no rating, no correction. Any "quality signal" derived automatically
would therefore be invented. What the tables *do* contain is four honest proxies
for a question that went badly, and a person deciding which of those are worth
adding to the golden set is the mechanism. A smaller honest loop beats a larger
speculative one.

The signals, in descending order of confidence:

1. **A documentation search that found nothing.** The strongest signal there is:
   somebody asked something the documentation does not answer, and the assistant
   knows it. ``search_docs`` writes ``no_results`` onto its audit row precisely so
   this is queryable without parsing message payloads.
2. **A documentation tool that errored or was denied.** A bad path, a missing
   page, a half-copied image.
3. **A turn that produced no answer.** Ran out of its request limit, or failed.
   Whatever the cause, the user got nothing.
4. **A product question answered without opening any documentation.** The
   weakest of the four and the only heuristic one: it matches the user's wording
   against product vocabulary, so it will over-report. It is here because it is
   the only signal that can catch a *confident* wrong answer, which is the
   failure the whole knowledge feature risks introducing — and a confident wrong
   answer leaves no trace anywhere else.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api" / "src"))

from api.models.assistant import (  # noqa: E402
    AssistantConversation,
    AssistantMessage,
    AssistantToolCall,
)

DOCS_TOOLS = ("search_docs", "read_doc_page")

# Words that mark a question as being about DuckHaven rather than about the
# user's data. Deliberately generous: signal 4 is a triage aid, and a candidate a
# human discards costs a second, while one never surfaced costs a blind spot.
_PRODUCT_VOCABULARY = re.compile(
    r"\b(duckhaven|iceberg|polaris|snapshot|time travel|catalog|worksheet|agent|"
    r"semantic model|storage backend|elastic|does it support|is it possible|"
    r"can i|how do i|supported|documentation)\b",
    re.IGNORECASE,
)


def _user_text(payload: list) -> str:
    """The user's own words from a stored turn, without loading the SDK."""
    parts = []
    for message in payload:
        if message.get("kind") != "request":
            continue
        for part in message.get("parts", []):
            if part.get("part_kind") == "user-prompt" and isinstance(part.get("content"), str):
                parts.append(part["content"])
    return " ".join(parts)


def _has_answer(payload: list) -> bool:
    return any(
        part.get("part_kind") == "text" and (part.get("content") or "").strip()
        for message in payload
        if message.get("kind") == "response"
        for part in message.get("parts", [])
    )


async def mine(db, since: datetime) -> dict[str, list[dict]]:
    findings: dict[str, list[dict]] = {
        k: []
        for k in (
            "unanswered_by_docs",
            "docs_tool_failed",
            "turn_produced_no_answer",
            "answered_without_docs",
        )
    }

    # Signals 1 and 2 come straight off the audit rows. The conversation is
    # joined in so a person can go and read the whole exchange.
    rows = (
        await db.execute(
            select(AssistantToolCall, AssistantConversation.title)
            .join(
                AssistantConversation,
                AssistantConversation.id == AssistantToolCall.conversation_id,
            )
            .where(AssistantToolCall.created_at >= since)
            .where(AssistantToolCall.tool.in_(DOCS_TOOLS))
            .order_by(AssistantToolCall.created_at.desc())
        )
    ).all()
    for call, title in rows:
        args = call.args if isinstance(call.args, dict) else {}
        entry = {
            "conversation": str(call.conversation_id),
            "conversation_title": title,
            "tool": call.tool,
            "at": call.created_at.isoformat(),
        }
        if call.tool == "search_docs" and call.detail == "no_results":
            findings["unanswered_by_docs"].append(entry | {"query": args.get("query", "")})
        elif call.status in ("error", "denied"):
            findings["docs_tool_failed"].append(
                entry | {"path": args.get("path", ""), "detail": call.detail or ""}
            )

    # Signals 3 and 4 need the turn itself.
    turns = (
        await db.execute(
            select(AssistantMessage, AssistantConversation.title)
            .join(
                AssistantConversation,
                AssistantConversation.id == AssistantMessage.conversation_id,
            )
            .where(AssistantMessage.created_at >= since)
            .order_by(AssistantMessage.conversation_id, AssistantMessage.ordinal)
        )
    ).all()

    # Docs calls per conversation, in order, so each turn can be given its own
    # window. Asking merely "did this conversation ever open a page?" would let
    # one cited turn vouch for every other turn beside it — which is exactly the
    # answer signal 4 is trying to find.
    docs_calls: dict[uuid.UUID, list] = {}
    for call, _ in rows:
        docs_calls.setdefault(call.conversation_id, []).append(call.created_at)

    previous_turn_at: dict[uuid.UUID, object] = {}
    for message, title in turns:
        payload = message.payload or []
        question = _user_text(payload)
        lower = previous_turn_at.get(message.conversation_id)
        previous_turn_at[message.conversation_id] = message.created_at
        if not question:
            continue
        entry = {
            "conversation": str(message.conversation_id),
            "conversation_title": title,
            "question": question,
            "at": message.created_at.isoformat(),
        }
        if not _has_answer(payload):
            findings["turn_produced_no_answer"].append(entry)
            continue
        if not _PRODUCT_VOCABULARY.search(question):
            continue
        # A turn's tool calls share its commit, so they land in
        # (previous turn, this turn]. The same window the transcript renderer
        # uses to attribute SQL and citations.
        opened_docs = any(
            (lower is None or call_at > lower) and call_at <= message.created_at
            for call_at in docs_calls.get(message.conversation_id, [])
        )
        if not opened_docs:
            findings["answered_without_docs"].append(entry)
    return findings


def as_candidate_cases(findings: dict[str, list[dict]]) -> list[dict]:
    """Render the two signals that carry a real question as draft cases.

    The other two name a fault rather than a question, so they are reported for
    a human to read but are not shaped as cases: a case needs a question somebody
    actually asked.
    """
    cases = []
    for i, item in enumerate(findings["unanswered_by_docs"]):
        if not item.get("query"):
            continue
        cases.append(
            {
                "name": f"mined_no_results_{i}",
                "inputs": {"question": item["query"]},
                "metadata": {
                    "category": "unanswerable",
                    "provenance": "hand",
                    "negative": True,
                    "expect_refusal": True,
                    "note": (
                        "Mined from production: documentation search found nothing. "
                        "Confirm the docs really do not cover this before keeping it "
                        "as a refusal case — if they do, this is a retrieval bug, not "
                        "a negative case."
                    ),
                },
            }
        )
    for i, item in enumerate(findings["answered_without_docs"]):
        cases.append(
            {
                "name": f"mined_uncited_{i}",
                "inputs": {"question": item["question"]},
                "metadata": {
                    "category": "product_knowledge",
                    "provenance": "hand",
                    "negative": False,
                    "expected_tools_any": ["search_docs", "read_doc_page"],
                    "note": (
                        "Mined from production: a product question answered without "
                        "opening any documentation. Check the answer was actually "
                        "right before adding this - it may have been correct from the "
                        "resident block, in which case it is not a finding."
                    ),
                },
            }
        )
    return cases


def report(findings: dict[str, list[dict]], since: datetime) -> str:
    lines = [f"Assistant feedback since {since:%Y-%m-%d}", ""]
    labels = {
        "unanswered_by_docs": "Documentation search found nothing",
        "docs_tool_failed": "A documentation tool errored or was denied",
        "turn_produced_no_answer": "The turn produced no answer",
        "answered_without_docs": "Product question answered without opening docs (heuristic)",
    }
    for key, label in labels.items():
        items = findings[key]
        lines.append(f"{label}: {len(items)}")
        for item in items[:10]:
            detail = item.get("query") or item.get("question") or item.get("path") or ""
            lines.append(f"  - {detail[:100]}")
        if len(items) > 10:
            lines.append(f"  … and {len(items) - 10} more")
        lines.append("")
    return "\n".join(lines)


async def main_async(days: int, out: Path | None) -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    since = datetime.now(UTC) - timedelta(days=days)

    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            # Read-only by intent; make it so at the database, not just by habit.
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SET TRANSACTION READ ONLY"))
            findings = await mine(db, since)
    finally:
        await engine.dispose()

    print(report(findings, since))
    cases = as_candidate_cases(findings)
    if out and cases:
        header = (
            "# Mined by scripts/assistant-mine-feedback.py. NOT the golden set:\n"
            "# read each note, confirm the finding, and promote by hand into\n"
            "# api/tests/evals/cases.yaml. Delete the rest.\n"
        )
        out.write_text(
            header + yaml.dump({"cases": cases}, sort_keys=False, allow_unicode=True, width=96)
        )
        print(f"{len(cases)} candidate cases -> {out}")
    elif out:
        print("No candidate cases to write.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="how far back to look")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("api/tests/evals/cases.candidate.yaml"),
        help="where to write candidate cases",
    )
    parser.add_argument(
        "--no-out", action="store_true", help="report only; write no candidate file"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args.days, None if args.no_out else args.out)))


if __name__ == "__main__":
    main()
