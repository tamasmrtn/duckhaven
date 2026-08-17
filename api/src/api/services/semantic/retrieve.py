"""Which definitions is this question about?

Deliberately lexical, and deliberately not embeddings.

The evidence for that is fairly direct. Neither researched platform retrieves
*within* a semantic model: Snowflake ships the whole model to the language model
under a 2 MB cap and recommends keeping it to about ten tables, and Databricks
has a person scope a Genie space to about five tables. Both then route *between*
models. Bounding the model turned out to be the accuracy mechanism, and once the
model is bounded there is very little left for a vector index to do — the
candidate set is dozens of named concepts, not millions of documents.

So the job here is routing plus vocabulary: match what somebody said ("turnover",
"GMV", "clients") to what a definition is called, and rank. That is what synonyms
are for, and a synonym match is a stronger signal than any distance metric
because a person wrote it down on purpose.

Ranking runs in Python over an already-bounded set rather than in SQL, because
the unit suite runs on SQLite and ``tsvector``/``pg_trgm`` do not exist there.
The honest scale note: this is fine to a few hundred models per workspace. Past
that the fix is a Postgres full-text index over a materialised search column —
still not embeddings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.services.semantic.model import LoadedModel

# Words that carry no signal in an analytical question. Kept short on purpose: an
# aggressive stop list starts eating real vocabulary ("count", "total", "average"
# are all plausible metric names).
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "for",
        "by",
        "in",
        "on",
        "at",
        "to",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "how",
        "what",
        "which",
        "who",
        "when",
        "where",
        "we",
        "our",
        "us",
        "i",
        "me",
        "my",
        "you",
        "your",
        "show",
        "give",
        "get",
        "list",
        "find",
        "tell",
        "many",
        "much",
        "please",
    }
)

_WORD = re.compile(r"[a-z0-9]+")

# Exact name and exact synonym dominate everything else. The gap is wide because
# a definition literally called what the user said is not "quite similar" to one
# whose description happens to share a word — it is the answer.
_SCORE_EXACT_NAME = 100.0
_SCORE_EXACT_SYNONYM = 90.0
_SCORE_NAME_TOKEN = 12.0
_SCORE_SYNONYM_TOKEN = 10.0
_SCORE_LABEL_TOKEN = 6.0
_SCORE_DESCRIPTION_TOKEN = 2.0
_SCORE_MODEL_TOKEN = 1.0


def tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def _phrases(text: str) -> set[str]:
    """The question itself, plus every contiguous run of its words.

    Lets a two-word definition name like "active customers" match inside a longer
    question without needing the whole sentence to be the name.
    """
    words = [w for w in _WORD.findall(text.lower())]
    out: set[str] = set()
    for size in (1, 2, 3):
        for i in range(len(words) - size + 1):
            out.add(" ".join(words[i : i + size]))
    out.add(" ".join(words))
    return out


def _normalise(name: str) -> str:
    """``active_customers`` and ``Active Customers`` are the same phrase."""
    return " ".join(_WORD.findall(name.lower()))


@dataclass
class Hit:
    """One matching definition, with enough context to act on it."""

    kind: str
    model: str
    name: str
    label: str
    description: str | None
    synonyms: tuple[str, ...]
    status: str
    score: float
    detail: dict

    def as_dict(self) -> dict:
        out = {
            "kind": self.kind,
            "model": self.model,
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "synonyms": list(self.synonyms),
            "status": self.status,
        }
        out.update(self.detail)
        return out


def _score(
    question_tokens: set[str],
    question_phrases: set[str],
    *,
    name: str,
    label: str,
    description: str | None,
    synonyms: tuple[str, ...],
    model_tokens: set[str],
) -> float:
    score = 0.0

    if _normalise(name) in question_phrases or _normalise(label) in question_phrases:
        score += _SCORE_EXACT_NAME
    for synonym in synonyms:
        if _normalise(synonym) in question_phrases:
            score += _SCORE_EXACT_SYNONYM
            break

    name_tokens = tokens(name) | tokens(label)
    score += _SCORE_NAME_TOKEN * len(question_tokens & name_tokens)

    synonym_tokens: set[str] = set()
    for synonym in synonyms:
        synonym_tokens |= tokens(synonym)
    score += _SCORE_SYNONYM_TOKEN * len(question_tokens & synonym_tokens)

    score += _SCORE_DESCRIPTION_TOKEN * len(question_tokens & tokens(description))
    score += _SCORE_MODEL_TOKEN * len(question_tokens & model_tokens)
    return score


def search(models: list[LoadedModel], question: str, *, limit: int = 10) -> list[Hit]:
    """Rank the metrics and dimensions a question is plausibly about.

    Metrics outrank dimensions at equal score: a question is usually about *what*
    to measure, and the dimension is how to break it down. Ties break on
    ``(model, name)`` so the same question always returns the same order — an
    assistant that gets a different answer to the same question on two runs is
    impossible to debug.
    """
    question_tokens = tokens(question)
    question_phrases = _phrases(question)
    if not question_tokens:
        return []

    hits: list[Hit] = []
    for model in models:
        model_tokens = tokens(model.name) | tokens(model.slug) | tokens(model.description)

        for metric in model.metrics.values():
            score = _score(
                question_tokens,
                question_phrases,
                name=metric.name,
                label=metric.label,
                description=metric.description,
                synonyms=metric.synonyms,
                model_tokens=model_tokens,
            )
            if score <= 0:
                continue
            hits.append(
                Hit(
                    kind="metric",
                    model=model.slug,
                    name=metric.name,
                    label=metric.label,
                    description=metric.description,
                    synonyms=metric.synonyms,
                    status=metric.status,
                    score=score,
                    detail={
                        "expression": metric.render(),
                        "time_dimension": metric.time_dimension,
                        "caveat": metric.caveat,
                    },
                )
            )

        for dim in model.dimensions.values():
            score = _score(
                question_tokens,
                question_phrases,
                name=dim.name,
                label=dim.label,
                description=dim.description,
                synonyms=dim.synonyms,
                model_tokens=model_tokens,
            )
            if score <= 0:
                continue
            hits.append(
                Hit(
                    kind="dimension",
                    model=model.slug,
                    name=dim.name,
                    label=dim.label,
                    description=dim.description,
                    synonyms=dim.synonyms,
                    status="published",
                    score=score,
                    detail={
                        "dimension_kind": dim.kind,
                        "sample_values": list(dim.sample_values[:5]),
                    },
                )
            )

    hits.sort(key=lambda h: (-h.score, h.kind != "metric", h.model, h.name))
    return hits[:limit]


def ambiguous(hits: list[Hit]) -> list[Hit]:
    """Metric hits that are tied at the top — the ones a person must choose between.

    "How many customers do we have?" against a model defining both
    ``total_customers`` and ``active_customers`` should produce a question, not a
    number. Returning the tie explicitly is what lets the assistant ask instead of
    silently taking the first row.
    """
    metrics = [h for h in hits if h.kind == "metric"]
    if len(metrics) < 2:
        return []
    top = metrics[0].score
    tied = [h for h in metrics if h.score == top]
    return tied if len(tied) > 1 else []
