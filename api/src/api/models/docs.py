"""The documentation corpus, loaded into Postgres so it can be searched.

The rows are a *cache* of files the image already carries, not a source of
truth — ``docs/`` is. They exist only because full-text ranking needs an index,
and they are rebuilt wholesale whenever the shipped corpus changes, keyed by a
content hash (see ``knowledge/sync.py``).

The ``search`` tsvector and its GIN index are Postgres-only and live in the
migration rather than here: there is no SQLite equivalent, and the unit suite
runs on SQLite. Nothing in the ORM needs to see that column — the search query
names it directly — so leaving it out keeps ``create_all`` working on both.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class DocsPage(Base):
    """One documentation page, keyed by its path relative to ``docs/``."""

    __tablename__ = "docs_pages"

    path: Mapped[str] = mapped_column(String(255), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    section: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)


class DocsCorpusMeta(Base):
    """Which corpus is currently loaded, so a replica can skip a no-op reload.

    Single row, ``id = 1``. The hash covers the index and every page body, so a
    rolling deploy converges: whichever replica boots with a newer image notices
    the mismatch and rebuilds, and the others then match on their next start.
    """

    __tablename__ = "docs_corpus_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
