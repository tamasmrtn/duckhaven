from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# The unit suite builds its schema with ``create_all`` on SQLite, and the models
# declare ``postgresql.UUID``. SQLite has no UUID type, so the bare name would
# take NUMERIC affinity: an all-digit 32-character hex, or one with a single
# ``e`` between digits, is stored as an INTEGER/REAL and read back as a number,
# which the result processor then rejects. That is rare per value — roughly one
# uuid4 in a million — and therefore a flake rather than a failure: one CI run
# seeded it and took the scheduler tests down. CHAR(32) is what SQLAlchemy's own
# generic ``Uuid`` emits on SQLite: TEXT affinity, the same ``value.hex`` the
# bind processor already writes, and the same reader. Postgres DDL is untouched,
# and migrations name their own types.
@compiles(PostgresUUID, "sqlite")
def _postgres_uuid_as_char(type_, compiler, **kw) -> str:
    return "CHAR(32)"
