"""Server capability registry — the single source of truth behind ``GET /api/version``.

Clients feature-detect against ``FEATURES`` rather than parsing the release
version: a slug being present means the behaviour is available, and its absence
(including an older server with no ``/version`` endpoint at all) means the client
must assume the oldest supported behaviour. Slugs are therefore **additive and
stable** — never rename or remove one, because that silently changes what an old
client concludes.

``API_VERSION`` is the negotiated *contract* version. Unlike the release version
(which tracks the git tag and moves every release), it is a hand-bumped integer
that changes only on a breaking change to the API contract.
"""

# Bump only on a breaking change to the API contract — not per release.
API_VERSION = 1

# Additive and stable: append new slugs, never rename or remove existing ones.
FEATURES: tuple[str, ...] = (
    # queries.py returns a typed ``column_schema`` on query results (#175).
    "column_schema",
    # statement_policy.py admits ``TRUNCATE TABLE`` (#172).
    "truncate_table",
    # A bare ``DESCRIBE`` materializes without a ``SELECT * FROM (…)`` wrapper (#171).
    "meta_statement_materialization",
)
