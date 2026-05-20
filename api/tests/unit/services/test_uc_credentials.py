"""Unit tests for `api.services.uc_credentials`.

Covers the cache contract (hit / miss / half-TTL refresh / concurrent
get / eviction-on-None) and the `vend_workspace_creds` fetcher's
short-circuits for local backends and empty workspaces.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fake_uc import FakeUC

from api.services.uc_credentials import (
    CredCache,
    Creds,
    vend_workspace_creds,
)
from api.services.unity_catalog import UCTemporaryCredentials


def _expire_in(seconds: float) -> datetime:
    return datetime.now(tz=UTC) + timedelta(seconds=seconds)


def _creds(seconds: float = 3600.0) -> Creds:
    return Creds(
        kind="s3",
        fields={"access_key_id": "k", "secret_access_key": "s"},
        expires_at=_expire_in(seconds),
    )


# --- CredCache ---


async def test_cache_miss_calls_fetcher_and_returns_result():
    cache = CredCache(safety_window_s=300)
    fetched: list[int] = []

    async def fetch():
        fetched.append(1)
        return _creds(3600)

    out = await cache.get_or_fetch("key1", fetch)
    assert out is not None
    assert len(fetched) == 1


async def test_cache_hit_skips_fetcher_when_fresh():
    cache = CredCache(safety_window_s=300)
    fresh = _creds(3600)

    async def fetch():
        return fresh

    await cache.get_or_fetch("key1", fetch)

    called = []

    async def fetch2():
        called.append(1)
        return _creds(3600)

    out = await cache.get_or_fetch("key1", fetch2)
    assert out is fresh
    assert called == []  # not invoked


async def test_cache_refresh_when_inside_safety_window():
    cache = CredCache(safety_window_s=300)

    near_expiry = Creds(
        kind="s3",
        fields={"k": "v"},
        expires_at=_expire_in(60),  # less than 300s safety window
    )
    fresh = _creds(3600)

    async def fetch_old():
        return near_expiry

    async def fetch_new():
        return fresh

    await cache.get_or_fetch("key1", fetch_old)
    out = await cache.get_or_fetch("key1", fetch_new)
    assert out is fresh


async def test_concurrent_get_only_fetches_once():
    cache = CredCache(safety_window_s=300)
    fetch_calls = 0

    async def slow_fetch():
        nonlocal fetch_calls
        fetch_calls += 1
        await asyncio.sleep(0.05)
        return _creds(3600)

    results = await asyncio.gather(
        cache.get_or_fetch("key", slow_fetch),
        cache.get_or_fetch("key", slow_fetch),
        cache.get_or_fetch("key", slow_fetch),
    )
    assert all(r is not None for r in results)
    assert fetch_calls == 1


async def test_independent_keys_do_not_collide():
    cache = CredCache(safety_window_s=300)

    async def fetch_a():
        return Creds(kind="s3", fields={"a": 1}, expires_at=_expire_in(3600))

    async def fetch_b():
        return Creds(kind="azure", fields={"b": 2}, expires_at=_expire_in(3600))

    a = await cache.get_or_fetch("ws_a", fetch_a)
    b = await cache.get_or_fetch("ws_b", fetch_b)
    assert a is not None and a.kind == "s3"
    assert b is not None and b.kind == "azure"


async def test_fetcher_returning_none_evicts_existing_entry():
    cache = CredCache(safety_window_s=300)

    async def fetch_some():
        return Creds(kind="s3", fields={"k": "v"}, expires_at=_expire_in(60))

    async def fetch_none():
        return None

    await cache.get_or_fetch("key", fetch_some)
    out = await cache.get_or_fetch("key", fetch_none)
    assert out is None
    # And a second get_or_fetch with a working fetcher would fetch again.
    new_creds = _creds(3600)
    out2 = await cache.get_or_fetch("key", lambda: _async_return(new_creds))
    assert out2 is new_creds


async def _async_return(value):
    return value


# --- vend_workspace_creds ---


@pytest.mark.parametrize("kind", ["local_fs", "nas"])
async def test_vend_returns_none_for_local_backends(kind: str):
    uc = FakeUC()
    out = await vend_workspace_creds(uc, "ws_local", kind)
    assert out is None


async def test_vend_returns_none_for_empty_workspace():
    """No tables yet -> no anchor for table-scope vending."""
    uc = FakeUC()
    await uc.create_catalog("ws_cloud")
    await uc.create_schema("ws_cloud", "main")
    out = await vend_workspace_creds(uc, "ws_cloud", "s3")
    assert out is None


async def test_vend_returns_s3_creds_when_anchor_exists():
    uc = FakeUC()
    await uc.create_catalog("ws_cloud")
    await uc.create_schema("ws_cloud", "main")
    await uc.create_table(
        catalog="ws_cloud",
        schema="main",
        name="events",
        columns=[
            {
                "name": "id",
                "type_text": "int",
                "type_name": "INT",
                "type_json": "",
                "position": 0,
                "nullable": False,
            }
        ],
        storage_location="s3://bucket/ws_cloud/main/events/",
    )
    out = await vend_workspace_creds(uc, "ws_cloud", "s3")
    assert out is not None
    assert out.kind == "s3"
    assert out.fields["access_key_id"] == "fake-key"


async def test_vend_returns_none_when_uc_yields_wrong_cloud():
    """Backend kind=adls_gen2 but UC vends aws creds — caller should refuse."""

    class _BadUC(FakeUC):
        async def gen_temp_creds(self, *, table_id, operation="READ_WRITE"):
            return UCTemporaryCredentials(
                aws_temp_credentials={"access_key_id": "x", "secret_access_key": "y"},
                azure_user_delegation_sas=None,
                expiration_time="2099-01-01T00:00:00Z",
            )

    uc = _BadUC()
    await uc.create_catalog("ws_az")
    await uc.create_schema("ws_az", "main")
    await uc.create_table(
        catalog="ws_az",
        schema="main",
        name="events",
        columns=[
            {
                "name": "id",
                "type_text": "int",
                "type_name": "INT",
                "type_json": "",
                "position": 0,
                "nullable": False,
            }
        ],
        storage_location="abfss://x@y.dfs/main/events/",
    )
    out = await vend_workspace_creds(uc, "ws_az", "adls_gen2")
    assert out is None
