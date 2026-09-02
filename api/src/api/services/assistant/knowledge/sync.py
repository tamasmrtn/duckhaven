"""Load the shipped documentation corpus into Postgres, once per release.

Called at startup. The tables are a search index over files the image already
carries, so this is a cache fill, not a migration: it is keyed by a hash of the
index plus every page body, and a replica whose hash already matches does
nothing.

Two properties matter for a rolling deploy. It takes a **transaction-scoped
advisory lock**, so several replicas booting at once do the work exactly once
rather than deadlocking on each other's deletes. And it **replaces the corpus
wholesale** rather than diffing, because a page removed from ``docs/`` must
disappear from search — a partial update would leave the assistant citing a page
its own build no longer has.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.docs import DocsCorpusMeta, DocsPage
from api.services.assistant.knowledge.loader import DocsUnavailableError, docs_dir, load_index

logger = logging.getLogger(__name__)

# Arbitrary but fixed: Postgres advisory locks are a single global namespace, so
# the value only has to be unique within DuckHaven.
_LOCK_KEY = 0x0D0C5_1DEE


def _corpus() -> tuple[str, list[dict]]:
    """The shipped pages and a hash identifying them collectively."""
    directory = docs_dir()
    digest = hashlib.sha256()
    pages: list[dict] = []
    for page in load_index().pages:
        body = (directory / page.path).read_text()
        digest.update(page.path.encode())
        digest.update(body.encode())
        pages.append(
            {
                "path": page.path,
                "title": page.title,
                "section": page.section,
                "summary": page.summary,
                "body": body,
            }
        )
    return digest.hexdigest(), pages


async def sync_corpus(db: AsyncSession) -> bool:
    """Ensure the loaded corpus matches this build. Returns whether it reloaded.

    Best-effort by contract: a deployment without a docs tree, or one whose
    database refuses the write, keeps serving. Search then returns nothing, which
    ``search_docs`` reports honestly as "the documentation does not cover this"
    — worse than working search, better than a replica that will not boot.
    """
    try:
        content_hash, pages = _corpus()
    except DocsUnavailableError, OSError:
        logger.warning("No documentation corpus to load; assistant search will be empty.")
        return False

    current = (await db.execute(select(DocsCorpusMeta).limit(1))).scalar_one_or_none()
    if current is not None and current.content_hash == content_hash:
        return False

    if db.bind.dialect.name == "postgresql":
        # Transaction-scoped: released on commit or rollback, so a crashed replica
        # cannot strand the lock. A concurrent replica blocks here, then finds the
        # hash already current below and does nothing.
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
        current = (await db.execute(select(DocsCorpusMeta).limit(1))).scalar_one_or_none()
        if current is not None and current.content_hash == content_hash:
            return False

    await db.execute(delete(DocsPage))
    for page in pages:
        db.add(DocsPage(**page))
    await db.execute(delete(DocsCorpusMeta))
    db.add(
        DocsCorpusMeta(
            id=1,
            content_hash=content_hash,
            page_count=len(pages),
            app_version=settings.app_version,
        )
    )
    await db.commit()
    logger.info("Loaded %d documentation pages for assistant search.", len(pages))
    return True
