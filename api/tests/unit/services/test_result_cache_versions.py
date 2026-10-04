"""Table versions for the result cache: the Iceberg + Polaris side of the catalog
seam (`table_versions`, `data_equivalent`, `routines_version`). The DuckLake side
reads Postgres-specific metadata SQL and is covered by the integration suite."""

from __future__ import annotations

from fake_polaris import FakePolaris

from api.models.catalog import Catalog
from api.services.catalog_backends import TableVersion
from api.services.catalog_backends.polaris import PolarisCatalogBackend
from api.services.polaris import PolarisSnapshot, PolarisTable, PolarisTableVersion


def _catalog() -> Catalog:
    return Catalog(slug="c1", name="c1", polaris_name="wh", kind="iceberg_polaris")


def _seed(fake: FakePolaris, name: str, **version) -> None:
    fake.tables[("wh", "main", name)] = PolarisTable(
        name=name, catalog_name="wh", schema_name="main"
    )
    if version:
        fake.versions[("wh", "main", name)] = PolarisTableVersion(**version)


async def test_versions_cover_snapshot_schema_and_table_identity() -> None:
    fake = FakePolaris()
    _seed(fake, "t", table_uuid="u1", snapshot_id=7, schema_id=2, metadata_location="m/00003")
    versions = await PolarisCatalogBackend(fake).table_versions(_catalog(), [("main", "t")])
    assert versions == {("main", "t"): TableVersion(content_id="u1:7:2", version_token="m/00003")}


async def test_a_missing_table_or_view_is_absent() -> None:
    fake = FakePolaris()
    _seed(fake, "t")
    versions = await PolarisCatalogBackend(fake).table_versions(
        _catalog(), [("main", "t"), ("main", "a_view")]
    )
    assert set(versions) == {("main", "t")}


def _snap(snapshot_id: int, parent: int | None, operation: str) -> PolarisSnapshot:
    return PolarisSnapshot(
        snapshot_id=snapshot_id,
        parent_snapshot_id=parent,
        timestamp_ms=snapshot_id,
        operation=operation,
    )


async def _equivalent(history: list[PolarisSnapshot], old: str, new: str) -> bool:
    fake = FakePolaris()
    _seed(fake, "t")
    fake.snapshots[("wh", "main", "t")] = history
    return await PolarisCatalogBackend(fake).data_equivalent(
        _catalog(), "main", "t", TableVersion(old, "a"), TableVersion(new, "b")
    )


async def test_compaction_since_the_cached_snapshot_is_equivalent() -> None:
    history = [_snap(3, 2, "replace"), _snap(2, 1, "replace"), _snap(1, None, "append")]
    assert await _equivalent(history, "u:1:0", "u:3:0")


async def test_an_append_since_the_cached_snapshot_is_a_change() -> None:
    history = [_snap(3, 2, "replace"), _snap(2, 1, "append"), _snap(1, None, "append")]
    assert not await _equivalent(history, "u:1:0", "u:3:0")


async def test_a_snapshot_that_is_not_an_ancestor_is_a_change() -> None:
    """Rolled back to a sibling branch: the cached snapshot is never reached."""
    history = [_snap(3, 1, "replace"), _snap(2, 1, "append"), _snap(1, None, "append")]
    assert not await _equivalent(history, "u:2:0", "u:3:0")


async def test_a_schema_change_or_a_recreated_table_is_a_change() -> None:
    history = [_snap(2, 1, "replace"), _snap(1, None, "append")]
    assert not await _equivalent(history, "u:1:0", "u:2:1")
    assert not await _equivalent(history, "u:1:0", "v:2:0")


async def test_expired_history_is_a_change() -> None:
    """The walk cannot prove anything once the snapshots between are expired."""
    history = [_snap(3, 2, "replace")]
    assert not await _equivalent(history, "u:1:0", "u:3:0")


async def test_an_empty_table_gaining_data_is_a_change() -> None:
    history = [_snap(1, None, "append")]
    assert not await _equivalent(history, "u:None:0", "u:1:0")


async def test_iceberg_catalogs_store_no_routines() -> None:
    assert await PolarisCatalogBackend(FakePolaris()).routines_version(_catalog()) is None
