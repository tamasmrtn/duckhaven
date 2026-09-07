"""Full-text search over the documentation corpus.

Postgres full-text over the weighted tsvector the migration generates, with
``ts_headline`` supplying the matched fragment. The query text is bound as a
parameter and parsed by ``websearch_to_tsquery``, which accepts what a person
would type — quoted phrases, ``or``, a leading ``-`` — and never raises on
malformed input, unlike ``to_tsquery``.

Two choices here were measured rather than assumed, against the golden cases in
``api/tests/evals/cases.yaml``:

**The query is OR-ed, not AND-ed.** ``websearch_to_tsquery`` requires every term,
so a natural-language question ("how do I query a table as it was last Tuesday?")
matches nothing at all — recall@5 was 0.00. Rewriting the parsed tsquery's ``&``
to ``|`` is what makes questions answerable.

That rewrite is only sound over a query with no negation in it, which is why
negation is stripped from the text first. ``&`` and ``|`` have the same arity, so
the *shape* of a parsed tsquery survives the swap — but the meaning of a negated
branch does not: ``A & !B`` ("A but not B") becomes ``A | !B`` ("A, or anything
that is not B"), which matches nearly the whole corpus at rank 0. A question
nothing should match would then come back as five alphabetically-first pages with
excerpts, and the tool's promise that an empty result means "the documentation
does not cover this" would be worthless.

**Ranking normalises by document length** (``ts_rank_cd``'s flag 2). Without it
the longest pages win everything an OR-query touches — ``concepts/architecture.md``
outranked ``concepts/storage-backends.md`` for the query "storage backends".

The honest limitation: Postgres full-text has no IDF, so a term that appears in
most of the corpus ("table", "catalog") pulls in noise, and a handful of cases
still miss. Ranking by how many distinct query terms a page matches trades recall
away for MRR, and recall is what matters when the model reads the top five and
picks. Current scores are asserted in ``api/tests/integration/test_docs_search.py``
rather than quoted here, so they cannot go stale.

Returns nothing on a dialect without full-text (the SQLite unit suite) rather
than raising: an empty result is a truthful "the documentation does not cover
this", and it keeps the tool testable without a database.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# A hyphen starting a token is websearch_to_tsquery's negation operator. Nobody
# asks documentation "X but not Y", so it is almost always an em-dash a user
# typed — and it cannot survive the &→| rewrite below. Hyphens *inside* a word
# ("read-only", "cost-based") are not preceded by whitespace and are left alone.
_NEGATION = re.compile(r"(?<!\S)-+")

_SEARCH = text("""
    WITH q AS (
        SELECT replace(
            websearch_to_tsquery('english', :q)::text, '&', '|'
        )::tsquery AS tq
    )
    SELECT path,
           title,
           section,
           summary,
           ts_headline(
               'english', body, q.tq,
               'MaxFragments=2, MinWords=8, MaxWords=22, ShortWord=3,'
               'StartSel=**, StopSel=**, FragmentDelimiter=" … "'
           ) AS excerpt,
           ts_rank_cd(search, q.tq, 2) AS rank
    FROM docs_pages, q
    WHERE search @@ q.tq
    ORDER BY rank DESC, path
    LIMIT :limit
""")


async def search_pages(db: AsyncSession, query: str, *, limit: int) -> list[dict]:
    """Rank the documentation pages a question is about."""
    terms = _NEGATION.sub(" ", query).strip()
    if not terms or db.bind.dialect.name != "postgresql":
        return []
    rows = (await db.execute(_SEARCH, {"q": terms, "limit": limit})).mappings().all()
    return [
        {
            "path": row["path"],
            "title": row["title"],
            "section": row["section"],
            "summary": row["summary"],
            "excerpt": " ".join(row["excerpt"].split()),
            "rank": round(float(row["rank"]), 6),
        }
        for row in rows
    ]
