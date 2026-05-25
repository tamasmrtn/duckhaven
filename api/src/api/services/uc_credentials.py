"""Short-lived storage credential cache with half-TTL refresh.

The cache is intentionally decoupled from UC. Callers provide a
`fetcher` callable that produces fresh `Creds | None`; the cache is
responsible for storing them per key, returning a cached copy while
the credential's remaining lifetime exceeds `safety_window_s`, and
de-duplicating concurrent fetches via per-key `asyncio.Lock`.

`vend_workspace_creds()` is the production fetcher: it picks any table
under the workspace's UC catalog and asks UC to mint
temporary-table-credentials for it. UC OSS 0.4 vends creds at table
scope; tables under the same backend root share the underlying
credential, so any table works as the vending anchor. Returns None for
local-fs / nas backends (no creds needed) and for empty workspaces
(nothing to anchor on yet — agent falls back to its own static creds).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from api.services.unity_catalog import UCClient, UCError

logger = logging.getLogger(__name__)


CredKind = Literal["s3", "azure"]


@dataclass(frozen=True)
class Creds:
    kind: CredKind
    fields: dict[str, Any]
    expires_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "fields": self.fields,
            "expires_at": self.expires_at.isoformat(),
        }


Fetcher = Callable[[], Awaitable[Creds | None]]


def _parse_expires(value: int | str | None) -> datetime:
    """Normalize UC's many `expiration_time` shapes to an aware UTC dt."""
    if value is None:
        # Be conservative: treat unknown TTL as already expired so the next
        # call refreshes.
        return datetime.now(tz=UTC)
    if isinstance(value, (int, float)):
        # UC OSS emits millis since epoch.
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    # ISO8601 string, possibly with trailing Z.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CredCache:
    """Per-key cache with half-TTL refresh and async-safe deduplication."""

    def __init__(
        self,
        *,
        safety_window_s: int,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._cache: dict[str, Creds] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._safety_window_s = safety_window_s
        self._clock = clock

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _still_fresh(self, creds: Creds) -> bool:
        remaining = (creds.expires_at - self._clock()).total_seconds()
        return remaining > self._safety_window_s

    async def get_or_fetch(self, key: str, fetcher: Fetcher) -> Creds | None:
        async with self._lock_for(key):
            cached = self._cache.get(key)
            if cached is not None and self._still_fresh(cached):
                return cached
            fresh = await fetcher()
            if fresh is not None:
                self._cache[key] = fresh
            elif cached is not None:
                # Fetcher returned None but we still had a cached copy;
                # evict it so callers see the new "no creds" answer.
                self._cache.pop(key, None)
            return fresh


async def vend_workspace_creds(
    uc: UCClient, workspace_slug: str, backend_kind: str
) -> Creds | None:
    """Mint short-lived creds for a workspace's storage backend.

    Returns None for local/NAS backends (no creds needed) and for
    workspaces that have no tables yet (no anchor for UC's
    table-scoped vending endpoint).
    """
    if backend_kind in ("local_fs", "nas"):
        return None

    try:
        schemas = await uc.list_schemas(workspace_slug)
    except UCError as exc:
        logger.warning("UC list_schemas failed for %s: %s", workspace_slug, exc)
        return None

    anchor_table_id: str | None = None
    for sc in schemas:
        try:
            tables = await uc.list_tables(workspace_slug, sc.name)
        except UCError:
            continue
        for tbl in tables:
            if tbl.table_id:
                anchor_table_id = tbl.table_id
                break
        if anchor_table_id is not None:
            break
    if anchor_table_id is None:
        return None

    try:
        uc_creds = await uc.gen_temp_creds(table_id=anchor_table_id, operation="READ_WRITE")
    except UCError as exc:
        logger.warning("UC gen_temp_creds failed for %s: %s", workspace_slug, exc)
        return None

    aws = uc_creds.aws_temp_credentials
    azure = uc_creds.azure_user_delegation_sas
    if backend_kind == "s3" and aws:
        expires_at = _parse_expires(uc_creds.expiration_time or aws.get("expiration_time"))
        return Creds(kind="s3", fields=aws, expires_at=expires_at)
    if backend_kind == "adls_gen2" and azure:
        expires_at = _parse_expires(uc_creds.expiration_time or azure.get("expiration_time"))
        return Creds(kind="azure", fields=azure, expires_at=expires_at)

    logger.warning(
        "UC vended creds without matching backend kind=%s for %s",
        backend_kind,
        workspace_slug,
    )
    return None
