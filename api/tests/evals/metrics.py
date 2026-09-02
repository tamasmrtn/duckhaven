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
        """Only the documentation pages — 'table:' sources are catalog cases."""
        return tuple(s for s in self.expected_sources if not s.startswith("table:"))


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


# Phrases that mark an answer as a refusal or an admission of ignorance. A
# deliberately crude screen, not the verdict: it runs free on every PR and
# catches an assistant that started answering questions it should decline. The
# judged layer scores the same cases on faithfulness, which is where nuance
# belongs.
_REFUSAL_MARKERS = (
    "does not support",
    "does not have",
    "doesn't have",
    "is not available",
    "isn't available",
    "not supported",
    "cannot",
    "can't",
    "no such",
    "i don't know",
    "i do not know",
    "not documented",
    "do not cover",
    "does not cover",
    "not currently",
    "requires your approval",
    "read-only",
    "could not access",
    "denied",
)


def looks_like_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def summarise(scores: dict[str, list[float]]) -> dict[str, float]:
    """Mean per group, so a regression can be localised to a category or slice."""
    return {
        group: round(sum(values) / len(values), 4) for group, values in scores.items() if values
    }
