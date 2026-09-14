"""UUID columns survive a round-trip through the unit suite's SQLite schema.

The models declare ``postgresql.UUID``. SQLite has no UUID type, so the type
name compiles to a bare ``UUID`` — a word with no INT/TEXT/CHAR/BLOB/REAL part,
which SQLite gives NUMERIC affinity. A 32-character hex string that is all
digits, or that has one ``e`` between digits and reads as scientific notation,
is then stored as a number and the result processor fails with ``'float' object
has no attribute 'replace'``. A random ``uuid4()`` hits one of those shapes
roughly once in a million values, so a suite that seeds thousands of rows
flakes occasionally rather than never.

``CHAR(32)`` is what SQLAlchemy's own generic ``Uuid`` emits on SQLite: TEXT
affinity, stores ``value.hex``, and the same result processor reads it back. The
regression is pinned with an all-digit UUID because that is the value class
NUMERIC affinity corrupts; a UUID containing a non-numeric letter always stored
as text.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.workspace import Workspace

# An all-digit hex, chosen so the test value states its own hazard rather than
# looking like an ordinary fixture.
ALL_DIGITS = uuid.UUID(hex="78748367514247217874836751424721")


async def test_an_all_digit_uuid_survives_the_sqlite_round_trip(db_engine):
    """Reading the row back is the failing operation: the INSERT succeeds even
    when the value is coerced, and only the refresh discovers the float."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as db:
        ws = Workspace(id=ALL_DIGITS, slug="uuid-flake", name="UUID flake")
        db.add(ws)
        await db.commit()

        await db.refresh(ws)

        assert ws.id == ALL_DIGITS


async def test_uuid_columns_have_text_affinity_on_sqlite(db_engine):
    """The mechanism, not just the symptom: ``typeof`` proves the column is
    TEXT, so no digit string can ever be read back as a number."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as db:
        db.add(Workspace(id=ALL_DIGITS, slug="uuid-affinity", name="Affinity"))
        await db.commit()

        affinity = (
            await db.execute(text("select typeof(id) from workspaces where slug = 'uuid-affinity'"))
        ).scalar_one()

        assert affinity == "text"
