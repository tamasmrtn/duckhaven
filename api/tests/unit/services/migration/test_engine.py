"""Engine state-machine tests with a fake Polaris and a stubbed relocate.

Storage IO (relocate) is stubbed so these exercise orchestration: phase
transitions, the atomic cutover (catalog re-point), failure leaving the catalog
untouched, cancellation, and crash-resume skipping already-registered tables.
"""

from __future__ import annotations

import uuid

import pytest

from api.models.catalog import Catalog
from api.models.catalog_migration import CatalogMigration, CatalogMigrationTable
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.services.migration import engine, relocate
from api.services.migration.relocate import RelocateResult


@pytest.fixture(autouse=True)
def _stub_relocate(monkeypatch):
    """Replace the real copy with a no-op that just rewrites the metadata URI."""

    def fake_relocate(*, source_metadata_location, old_prefix, new_prefix, **_):
        return RelocateResult(
            target_metadata_location=source_metadata_location.replace(old_prefix, new_prefix),
            bytes_copied=10,
        )

    monkeypatch.setattr(relocate, "relocate_table", fake_relocate)


async def _seed(db, fake, *, source_kind="object_store"):
    user = User(email=f"{uuid.uuid4().hex}@t.local", password_hash="x", name="A", role="user")
    db.add(user)
    await db.flush()
    source = StorageBackend(kind=source_kind, name="src", root_uri="/tmp/src", created_by=user.id)
    target = StorageBackend(
        kind="object_store", name="dst", root_uri="/tmp/dst", created_by=user.id
    )
    db.add_all([source, target])
    await db.flush()
    catalog = Catalog(
        slug="srccat",
        name="Src",
        polaris_name="srccat",
        storage_backend_id=source.id,
        created_by=user.id,
    )
    db.add(catalog)
    await db.flush()
    # Source side in Polaris: one schema, one table.
    await fake.create_catalog("srccat", storage_type="S3", base_location="s3://b/srccat")
    await fake.create_schema("srccat", "analytics")
    await fake.create_table(catalog="srccat", schema="analytics", name="t", columns=[])
    return user, source, target, catalog


async def test_happy_path_cuts_over(db_session, fake_polaris):
    user, source, target, catalog = await _seed(db_session, fake_polaris)
    mig = CatalogMigration(
        catalog_id=catalog.id,
        source_storage_backend_id=source.id,
        target_storage_backend_id=target.id,
        created_by=user.id,
    )
    db_session.add(mig)
    await db_session.commit()

    await engine.process_migration(db_session, fake_polaris, mig)
    await db_session.refresh(mig)
    await db_session.refresh(catalog)

    assert mig.status == "completed"
    assert mig.tables_total == 1 and mig.tables_done == 1
    # Atomic cutover: catalog now points at the shadow + target backend.
    assert catalog.polaris_name == mig.shadow_polaris_name
    assert catalog.storage_backend_id == target.id
    assert mig.source_polaris_name == "srccat"
    # The shadow table was registered and the probe cleaned up.
    assert (mig.shadow_polaris_name, "analytics", "t") in fake_polaris.tables
    assert (mig.shadow_polaris_name, "dh_migration", "probe") not in fake_polaris.tables


async def test_failure_leaves_catalog_on_old_backend(db_session, fake_polaris, monkeypatch):
    user, source, target, catalog = await _seed(db_session, fake_polaris)
    mig = CatalogMigration(
        catalog_id=catalog.id,
        source_storage_backend_id=source.id,
        target_storage_backend_id=target.id,
        created_by=user.id,
    )
    db_session.add(mig)
    await db_session.commit()

    def boom(**_):
        raise RuntimeError("storage unreachable")

    monkeypatch.setattr(relocate, "relocate_table", boom)
    await engine.process_migration(db_session, fake_polaris, mig)
    # The failure path rolls back, which expires every object in the session
    # (not just `mig`) — refresh `source` too before reading its attributes.
    await db_session.refresh(mig)
    await db_session.refresh(catalog)
    await db_session.refresh(source)

    assert mig.status == "failed"
    assert "storage unreachable" in (mig.error or "")
    assert catalog.polaris_name == "srccat"
    assert catalog.storage_backend_id == source.id


async def test_cancel_before_copy(db_session, fake_polaris):
    user, source, target, catalog = await _seed(db_session, fake_polaris)
    mig = CatalogMigration(
        catalog_id=catalog.id,
        source_storage_backend_id=source.id,
        target_storage_backend_id=target.id,
        created_by=user.id,
        cancel_requested=True,
    )
    db_session.add(mig)
    await db_session.commit()

    await engine.process_migration(db_session, fake_polaris, mig)
    await db_session.refresh(mig)
    await db_session.refresh(catalog)

    assert mig.status == "cancelled"
    assert catalog.polaris_name == "srccat"
    assert catalog.storage_backend_id == source.id


async def test_resume_skips_registered_tables(db_session, fake_polaris):
    user, source, target, catalog = await _seed(db_session, fake_polaris)
    shadow = "srccat__mdeadbeef"
    # A second source table so the resume has one already done + one to do.
    await fake_polaris.create_table(catalog="srccat", schema="analytics", name="t2", columns=[])
    # Pretend provisioning already happened: shadow catalog, probe, and t already
    # registered into the shadow on a prior (crashed) run.
    await fake_polaris.create_catalog(shadow, storage_type="S3", base_location="s3://b/sh")
    await fake_polaris.create_schema(shadow, "analytics")
    await fake_polaris.create_schema(shadow, "dh_migration")
    await fake_polaris.create_table(catalog=shadow, schema="dh_migration", name="probe", columns=[])
    await fake_polaris.register_table(shadow, "analytics", "t", "s3://b/sh/t/v1.metadata.json")

    mig = CatalogMigration(
        catalog_id=catalog.id,
        source_storage_backend_id=source.id,
        target_storage_backend_id=target.id,
        created_by=user.id,
        status="copying",
        shadow_polaris_name=shadow,
        source_polaris_name="srccat",
        tables_total=2,
        tables_done=1,
    )
    db_session.add(mig)
    await db_session.flush()
    db_session.add_all(
        [
            CatalogMigrationTable(
                migration_id=mig.id, schema_name="analytics", table_name="t", status="registered"
            ),
            CatalogMigrationTable(
                migration_id=mig.id, schema_name="analytics", table_name="t2", status="pending"
            ),
        ]
    )
    await db_session.commit()

    await engine.process_migration(db_session, fake_polaris, mig)
    await db_session.refresh(mig)
    await db_session.refresh(catalog)

    assert mig.status == "completed"
    assert mig.tables_done == 2
    assert catalog.polaris_name == shadow
    # Both shadow tables present; 't' was not re-registered (would have conflicted).
    assert (shadow, "analytics", "t2") in fake_polaris.tables
