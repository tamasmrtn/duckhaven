"""Tier 1b: documentation retrieval, scored against a real Postgres.

Free — no model, no provider key — but it cannot run in the unit suite, because
``api/tests/unit/conftest.py`` pins SQLite and there is no ``tsvector`` there.
So it lives here and runs in CI's ``integration`` job, which is gated on the same
path filter as the rest of the Python suite. Every pull request that touches
``api/`` or ``docs/`` scores retrieval.

What is asserted is the *aggregate*: recall@5 and MRR over the cases where
retrieval is supposed to succeed, reported separately by provenance. Negative
cases are excluded — their pages exist to give a judge ground truth, not to be
found by a search for words the documentation deliberately does not contain.

Auto-synthesised questions
were written from the page they point at, so they are trivially answerable by a
system that retrieved that page; scoring them together with hand-written ones
would flatter the index. Individual cases are allowed to miss — the thresholds
are what regress.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from api.config import settings
from api.services.assistant.knowledge import generate, sync
from api.services.assistant.knowledge.loader import load_index
from api.services.assistant.knowledge.search import search_pages
from tests.evals.metrics import load_cases, recall_at_k, reciprocal_rank, summarise

pytestmark = pytest.mark.integration

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0041_docs_pages_fts.py"
DOCS_DIR = generate._repo_root() / "docs"

# Thresholds, not per-case assertions, and set from measurement rather than
# aspiration. As committed the corpus scores recall@5 = 0.79 and MRR = 0.48 over
# the cases that name a page; the bars sit below that so they catch a regression
# instead of failing on the next reworded question. Raise them when retrieval
# actually improves — a threshold nobody can meet gets deleted, not fixed.
MIN_RECALL_AT_5 = 0.70
MIN_MRR = 0.40


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0041", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def corpus(pg_engine, monkeypatch):
    """A real Postgres carrying the real shipped corpus, indexed by the real
    migration — no hand-written DDL here, so the test cannot drift from it."""
    monkeypatch.setattr(settings, "assistant_docs_dir", DOCS_DIR)
    load_index.cache_clear()
    module = _load_migration()

    async with pg_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: setattr(
                module, "op", Operations(MigrationContext.configure(sync_conn))
            )
        )
        await conn.run_sync(lambda _: module.upgrade())

    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as db:
        await sync.sync_corpus(db)
        yield db
    load_index.cache_clear()


async def test_the_whole_corpus_is_indexed(corpus):
    loaded = (await corpus.execute(text("SELECT count(*) FROM docs_pages"))).scalar_one()

    assert loaded == len(load_index().pages)


async def test_the_search_vector_is_populated(corpus):
    """A generated column that is empty would make every query return nothing,
    silently — the tables would look fine."""
    empty = (
        await corpus.execute(text("SELECT count(*) FROM docs_pages WHERE search IS NULL"))
    ).scalar_one()

    assert empty == 0


async def test_a_title_match_outranks_a_passing_mention(corpus):
    """The weights, verified end to end: the page named after the term wins."""
    results = await search_pages(corpus, "storage backends", limit=5)

    assert results
    assert results[0]["path"] == "concepts/storage-backends.md"


async def test_results_carry_an_excerpt_showing_the_match(corpus):
    results = await search_pages(corpus, "time travel snapshot", limit=3)

    assert results
    assert all(r["excerpt"] for r in results)
    assert any("**" in r["excerpt"] for r in results)


async def test_nonsense_returns_nothing_rather_than_noise(corpus):
    """An empty result is the honest answer, and the tool reports it as one."""
    assert await search_pages(corpus, "zzzqqq nonexistentterm", limit=5) == []


async def test_a_malformed_query_does_not_raise(corpus):
    """websearch_to_tsquery accepts what a person types; to_tsquery would throw."""
    for query in ('"unclosed quote', "and or not", "-", "!!!"):
        await search_pages(corpus, query, limit=3)


async def test_retrieval_scores_meet_their_thresholds(corpus):
    cases = [c for c in load_cases() if c.retrieval_targets]
    recall: dict[str, list[float]] = {"hand": [], "auto": []}
    rr: dict[str, list[float]] = {"hand": [], "auto": []}
    misses: list[str] = []

    for case in cases:
        results = await search_pages(corpus, case.question, limit=5)
        paths = [r["path"] for r in results]
        hit = recall_at_k(paths, case.retrieval_targets, k=5)
        recall[case.provenance].append(hit)
        rr[case.provenance].append(reciprocal_rank(paths, case.retrieval_targets))
        if not hit:
            misses.append(f"{case.name}: wanted {case.retrieval_targets}, got {paths[:3]}")

    overall_recall = sum(sum(v) for v in recall.values()) / sum(len(v) for v in recall.values())
    overall_mrr = sum(sum(v) for v in rr.values()) / sum(len(v) for v in rr.values())

    # Reported split, because a question synthesised from a page is not evidence
    # of the same quality as one a person wrote without looking.
    print(f"\nrecall@5 by provenance: {summarise(recall)}")
    print(f"MRR by provenance:      {summarise(rr)}")
    print("misses:\n  " + "\n  ".join(misses or ["none"]))

    assert overall_recall >= MIN_RECALL_AT_5, f"recall@5 {overall_recall:.2f}; misses: {misses}"
    assert overall_mrr >= MIN_MRR, f"MRR {overall_mrr:.2f}"


async def test_hand_written_cases_are_scored_on_their_own(corpus):
    """The slice that carries the signal must clear the bar by itself, or a pile
    of easy synthesised questions could carry a failing index over the line."""
    cases = [c for c in load_cases() if c.retrieval_targets and c.provenance == "hand"]
    scores = [
        recall_at_k(
            [r["path"] for r in await search_pages(corpus, c.question, limit=5)],
            c.retrieval_targets,
            k=5,
        )
        for c in cases
    ]

    assert sum(scores) / len(scores) >= MIN_RECALL_AT_5
