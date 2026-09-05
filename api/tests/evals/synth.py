"""Draft candidate eval cases from the documentation index.

Run with ``make eval-synth``. Writes ``cases.candidate.yaml``; a human promotes
what is worth keeping into ``cases.yaml``. Nothing is added to the golden set
automatically, and that is the point — the synthesiser buys breadth cheaply, it
does not decide what the assistant is measured against.

Two rules, both because a synthesised question is weaker evidence than a written
one. Cases are marked ``provenance: auto`` and scored as their own slice, since a
question written *from* a page is trivially answerable by a system that retrieved
it. And it never drafts a **negative** case: asserting that something does not
exist requires knowing the whole product, so a model shown one page can only
invent it, and an invented negative is worse than none because it will be
trusted.

Costs a model call per page, so it is a deliberate act, not part of any suite.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import yaml
from pydantic_ai import Agent

from api.config import settings
from api.services.assistant.knowledge.loader import docs_dir, load_index

CANDIDATES_PATH = Path(__file__).with_name("cases.candidate.yaml")

# Deployment and operations pages answer an operator's questions, and belong in
# the set only once somebody actually asks one.
SYNTHESISABLE_SECTIONS = ("Concepts", "Guides", "Reference", "Getting started")

PROMPT = """\
You are drafting evaluation questions for a data platform's AI assistant.

Below is one page of DuckHaven's documentation. Write {n} questions a real user
would ask that this page answers.

Rules:
- Each question must be answerable from this page alone.
- Ask the way a user would, not the way the page is titled. "How do I query a
  table as it was last week?" — not "What is time travel?".
- Do not ask about anything the page does not state.
- Do not write questions whose answer is that a feature does not exist.
- One question per line, no numbering, no preamble.

PAGE: {path}
TITLE: {title}

{body}
"""


async def draft(model: str, path: str, title: str, body: str, n: int) -> list[str]:
    agent = Agent(model, output_type=str)
    result = await agent.run(PROMPT.format(n=n, path=path, title=title, body=body[:8000]))
    lines = [line.strip(" -•\t") for line in str(result.output).splitlines()]
    return [line for line in lines if line.endswith("?")][:n]


async def main_async(model: str, per_page: int, limit: int | None) -> None:
    index = load_index()
    directory = docs_dir()
    pages = [p for p in index.pages if p.section in SYNTHESISABLE_SECTIONS][:limit]

    existing = set()
    if CANDIDATES_PATH.exists():
        raw = yaml.safe_load(CANDIDATES_PATH.read_text()) or {}
        existing = {c["inputs"]["question"] for c in raw.get("cases", [])}

    cases: list[dict] = []
    for page in pages:
        body = (directory / page.path).read_text()
        for i, question in enumerate(await draft(model, page.path, page.title, body, per_page)):
            if question in existing:
                continue
            cases.append(
                {
                    "name": f"{page.path.removesuffix('.md').replace('/', '_')}_{i}",
                    "inputs": {"question": question},
                    "metadata": {
                        "category": "product_knowledge",
                        "provenance": "auto",
                        "negative": False,
                        "expected_sources": [page.path],
                        "expected_tools_any": ["search_docs", "read_doc_page"],
                    },
                }
            )
        print(f"  {page.path}")

    header = (
        "# Drafted by `make eval-synth`. NOT the golden set: promote what is worth\n"
        "# keeping into cases.yaml by hand, and delete the rest. Auto cases are\n"
        "# scored as their own slice — see cases.yaml for why.\n"
    )
    CANDIDATES_PATH.write_text(
        header + yaml.dump({"cases": cases}, sort_keys=False, allow_unicode=True, width=96)
    )
    print(f"\n{len(cases)} candidates -> {CANDIDATES_PATH.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=settings.assistant_model)
    parser.add_argument("--per-page", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None, help="only the first N pages")
    args = parser.parse_args()
    asyncio.run(main_async(args.model, args.per_page, args.limit))


if __name__ == "__main__":
    main()
