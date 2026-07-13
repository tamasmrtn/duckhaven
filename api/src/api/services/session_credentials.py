"""Per-session credential vending (the seam).

This centralizes what a session's held DuckDB connection uses to reach Polaris and
where a load may stage bulk Parquet. Today it vends the API's single shared Polaris
service principal — the same identity the agent used from its own config — but it
moves the **vend point to the API**, which is the hook a future per-principal
Polaris identity (and real STS-scoped staging credentials) plugs into. Governance
today rests on the API's per-statement authorization (``grants.assert_query_access``)
plus the ``catalog_grants`` ACL, not on the Polaris token's identity.

Deferred (env-gated integration tests): minting a distinct Polaris principal per
DuckHaven principal, and true short-lived STS credentials for the staging prefix
(the bundled MinIO backend has no STS — staging scoping there is the unique prefix
plus the statement policy that a ``COPY`` may only touch it).
"""

from __future__ import annotations

import uuid

from api.config import settings
from api.models import Catalog


def build_polaris_block() -> dict[str, str]:
    """The Polaris connection block put in the OPEN_SESSION frame so the agent's
    session connection builds its iceberg SECRET from API-vended credentials rather
    than its own static config. (Same identity today — the future per-principal
    hook replaces this body.)"""
    return {
        "endpoint": settings.polaris_base_url,
        "client_id": settings.polaris_client_id,
        "client_secret": settings.polaris_client_secret,
    }


def staging_uri_for(catalog: Catalog, session_id: uuid.UUID) -> str | None:
    """The scoped object-storage staging prefix for a session, under the active
    catalog's storage root: ``<root_uri>/<segment>/<session_id>/``. Returns
    ``None`` when the backend has no usable root URI."""
    root = (catalog.storage_backend.root_uri or "").rstrip("/")
    if not root:
        return None
    return f"{root}/{settings.sql_session_staging_prefix_segment}/{session_id}/"


def staging_prefixes(staging_uri: str | None) -> list[str]:
    """The prefixes the statement policy admits for COPY/read_* in this session."""
    return [staging_uri] if staging_uri else []
