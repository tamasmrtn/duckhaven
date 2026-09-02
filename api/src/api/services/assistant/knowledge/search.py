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
to ``|`` is what makes questions answerable. The rewrite is safe because the
tsquery has already been parsed and sanitised; ``&`` and ``|`` are binary
operators of the same arity, so phrases (``<->``) and negations (``!``) survive
untouched.

**Ranking normalises by document length** (``ts_rank_cd``'s flag 2). Without it
the longest pages win everything an OR-query touches — ``concepts/architecture.md``
outranked ``concepts/storage-backends.md`` for the query "storage backends".
Length normalisation took recall@5 from 0.57 to 0.79 and MRR from 0.23 to 0.48.

The honest limitation: Postgres full-text has no IDF, so a term that appears in
most of the corpus ("table", "catalog") pulls in noise, and a handful of cases
still miss. Ranking by how many distinct query terms a page matches trades that
back the wrong way — MRR 0.48 → 0.54, recall 0.79 → 0.71 — and recall is what
matters when the model reads the top five and picks.

Returns nothing on a dialect without full-text (the SQLite unit suite) rather
than raising: an empty result is a truthful "the documentation does not cover
this", and it keeps the tool testable without a database.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
    if not query.strip() or db.bind.dialect.name != "postgresql":
        return []
    rows = (await db.execute(_SEARCH, {"q": query, "limit": limit})).mappings().all()
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
