"""Retrieval and behaviour metrics — arithmetic, no model in the loop.

The whole point of a case file that states where its answer lives is that
retrieval quality becomes countable. Ragas and friends need an LLM for context
precision and recall only because they infer relevance from text; a curated case
that names its source page makes that unnecessary, and free.

This is the cheap early-warning layer: a broken index, a tokenizer change, or a
ranking regression shows up here in seconds, on every pull request, long before
anything a judge would notice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CASES_PATH = Path(__file__).with_name("cases.yaml")


@dataclass(frozen=True)
class Case:
    name: str
    question: str
    category: str
    provenance: str
    negative: bool
    expected_sources: tuple[str, ...]
    expected_tools_any: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    must_contain: tuple[str, ...]
    expect_refusal: bool
    note: str

    @property
    def doc_sources(self) -> tuple[str, ...]:
        """Pages that establish the truth for this question.

        What a judge should check the answer against. 'table:' sources name a
        catalog object rather than a page and are excluded.
        """
        return tuple(s for s in self.expected_sources if not s.startswith("table:"))

    @property
    def retrieval_targets(self) -> tuple[str, ...]:
        """Pages search is expected to *find* — which is not the same set.

        On a negative case the page is context for a judge, not a target for an
        index: ``row_level_security_does_not_exist`` names the permissions page
        so a scorer can tell "no, but here is the real grant model" from an
        invented policy syntax, but the docs never discuss row-level security,
        so a good index ranking that page highly for those words would be a
        coincidence rather than a success. Scoring recall over these cases
        measures the corpus's silence, and punishes retrieval for it — it took
        hand-written recall@5 from 0.75 to 0.54 the moment six were added.
        """
        return () if self.negative else self.doc_sources


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    raw = yaml.safe_load(path.read_text())
    cases = []
    for entry in raw["cases"]:
        meta = entry.get("metadata", {})
        cases.append(
            Case(
                name=entry["name"],
                question=entry["inputs"]["question"],
                category=meta.get("category", "uncategorised"),
                provenance=meta.get("provenance", "hand"),
                negative=bool(meta.get("negative", False)),
                expected_sources=tuple(meta.get("expected_sources", ())),
                expected_tools_any=tuple(meta.get("expected_tools_any", ())),
                forbidden_tools=tuple(meta.get("forbidden_tools", ())),
                must_contain=tuple(meta.get("must_contain", ())),
                expect_refusal=bool(meta.get("expect_refusal", False)),
                note=meta.get("note", ""),
            )
        )
    return cases


def recall_at_k(retrieved: list[str], expected: tuple[str, ...], k: int) -> float:
    """1.0 when any expected page is in the top k.

    Deliberately "any", not "all": a case names every page that *could* answer
    it, and finding one of them is a good retrieval. Requiring all would punish
    a correct ranking for the sin of having alternatives.
    """
    if not expected:
        return 1.0
    return 1.0 if set(retrieved[:k]) & set(expected) else 0.0


def reciprocal_rank(retrieved: list[str], expected: tuple[str, ...]) -> float:
    """1/rank of the first expected page, or 0.0 if it never appears."""
    if not expected:
        return 1.0
    for position, path in enumerate(retrieved, start=1):
        if path in expected:
            return 1.0 / position
    return 0.0


def called_expected_tool(called: list[str], case: Case) -> bool:
    if not case.expected_tools_any:
        return True
    return bool(set(called) & set(case.expected_tools_any))


def called_forbidden_tool(called: list[str], case: Case) -> bool:
    return bool(set(called) & set(case.forbidden_tools))


# Marks an answer as a refusal, an admission of ignorance, or a statement that
# something does not exist. A deliberately crude screen, not the verdict: it runs
# free on every PR and catches an assistant that has started answering questions
# it should decline. The judged layer scores the same cases on faithfulness,
# which is where nuance belongs.
#
# Biased towards *missing* refusals rather than inventing them. A false negative
# is a spurious failure someone investigates; a false positive lets a confidently
# wrong answer pass a negative case, which is the exact failure this whole
# feature risks introducing. When in doubt, do not match.
_REFUSAL = re.compile(
    r"""
      \b(?:does|do|did|is|are|was|were|can|could|will|would)\s+not\b
    | \b(?:doesn't|don't|didn't|isn't|aren't|can't|won't|cannot)\b
    | \bno\s+(?:such|way|equivalent|support|option|built-in|way\s+to)\b
    | \bthere\s+is\s+no\b
    | \bnot\s+(?:supported|available|documented|possible|currently|yet)\b
    | \bi\s+(?:don't|do\s+not)\s+know\b
    | \b(?:do|does)\s+not\s+cover\b
    | \bcould\s+not\s+access\b
    | \b(?:denied|read-only|requires\s+your\s+approval)\b
    | \broadmap\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def looks_like_refusal(answer: str) -> bool:
    return bool(_REFUSAL.search(answer))


# A documentation path as it appears in an answer: "reference/sql-support.md".
_CITED_PATH = re.compile(r"\b[a-z0-9-]+/[a-z0-9-]+\.md\b")


def cited_paths(answer: str) -> set[str]:
    """Documentation paths the answer names, whether or not they are real."""
    return set(_CITED_PATH.findall(answer))


def citation_presence(answer: str, indexed: set[str]) -> float | None:
    """1.0 when the answer cites at least one real page, 0.0 when it cites none.

    ``None`` when the answer names no path at all — not every product answer
    needs one, and scoring those as failures would push the assistant towards
    citing something for the sake of it.

    A cited path that is *not* in the index scores 0.0 rather than being ignored.
    The user sees citations as links, so an invented path is a broken link and a
    small confabulation in its own right.
    """
    named = cited_paths(answer)
    if not named:
        return None
    return 1.0 if named & indexed else 0.0


def summarise(scores: dict[str, list[float]]) -> dict[str, float]:
    """Mean per group, so a regression can be localised to a category or slice."""
    return {
        group: round(sum(values) / len(values), 4) for group, values in scores.items() if values
    }
