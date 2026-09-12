from fake_polaris import FakePolaris

from api.config import settings
from api.services.polaris import PolarisError
from api.services.workspace import ensure_polaris_catalog, polaris_storage


def test_object_store_empty_root_is_bucket_root():
    storage_type, base, extra = polaris_storage("object_store", "")
    assert storage_type == "S3"
    # The bundled bucket root; per-workspace isolation is added later by
    # ensure_polaris_catalog via the /{slug} scope.
    assert base == f"s3://{settings.s3_bucket}"
    assert extra == {
        "endpoint": settings.s3_endpoint,
        "endpointInternal": settings.s3_endpoint_internal,
        "pathStyleAccess": True,
        "region": settings.s3_region,
    }


def test_object_store_prefix_is_label_under_bucket():
    storage_type, base, extra = polaris_storage("object_store", "dept-finance/")
    assert storage_type == "S3"
    # root_uri is treated as a prefix label under the bundled bucket.
    assert base == f"s3://{settings.s3_bucket}/dept-finance"
    assert extra is not None and extra["endpoint"] == settings.s3_endpoint


def test_s3_kind_without_config_injects_nothing():
    storage_type, base, extra = polaris_storage("s3", "s3://my-bucket/duckhaven/")
    assert storage_type == "S3"
    assert base == "s3://my-bucket/duckhaven"
    # External operator-owned store: no bundled endpoint injected.
    assert extra is None


def test_s3_kind_builds_storage_config_from_config():
    storage_type, base, extra = polaris_storage(
        "s3",
        "s3://my-bucket/duckhaven/",
        {
            "role_arn": "arn:aws:iam::123456789012:role/duckhaven",
            "external_id": "dh-acme",
            "region": "us-east-1",
            "path_style_access": False,
        },
    )
    assert storage_type == "S3"
    assert base == "s3://my-bucket/duckhaven"
    assert extra == {
        "roleArn": "arn:aws:iam::123456789012:role/duckhaven",
        "region": "us-east-1",
        "externalId": "dh-acme",
        "pathStyleAccess": False,
    }


def test_adls_kind_builds_storage_config_from_config():
    storage_type, base, extra = polaris_storage(
        "adls_gen2",
        "abfss://c@acct.dfs.core.windows.net/duckhaven/",
        {"tenant_id": "00000000-0000-0000-0000-000000000000", "hierarchical": True},
    )
    assert storage_type == "AZURE"
    assert base == "abfss://c@acct.dfs.core.windows.net/duckhaven"
    assert extra == {
        "tenantId": "00000000-0000-0000-0000-000000000000",
        "hierarchical": True,
    }


# ── Endpoint reconciliation on an existing catalog ────────────────────────────
#
# Polaris records a catalog's storage config once, at creation, and vends it to
# DuckDB from then on. Without reconciliation, changing S3_ENDPOINT — or moving
# the bundled store to a different service name — leaves every existing catalog
# vending an address that no longer resolves.


async def _ensure(polaris, name="sales", **overrides):
    kwargs = {
        "storage_type": "S3",
        "base_location": f"s3://{settings.s3_bucket}",
        "extra_storage": {
            "endpoint": settings.s3_endpoint,
            "endpointInternal": settings.s3_endpoint_internal,
            "pathStyleAccess": True,
            "region": settings.s3_region,
        },
    }
    kwargs.update(overrides)
    await ensure_polaris_catalog(polaris, name, **kwargs)


async def test_stale_endpoints_on_an_existing_catalog_are_reconciled():
    polaris = FakePolaris()
    await polaris.create_catalog(
        "sales",
        storage_type="S3",
        base_location="s3://warehouse/sales",
        extra_storage={
            "endpoint": "http://old-host:9000",
            "endpointInternal": "http://old-host:9000",
        },
    )

    await _ensure(polaris)

    assert len(polaris.storage_updates) == 1
    _, config = polaris.storage_updates[0]
    assert config["endpoint"] == settings.s3_endpoint
    assert config["endpointInternal"] == settings.s3_endpoint_internal


async def test_reconciliation_preserves_the_rest_of_the_storage_config():
    """Only the endpoints are ours to rewrite; allowedLocations in particular
    must survive, or the catalog stops being able to address its own data."""
    polaris = FakePolaris()
    await polaris.create_catalog(
        "sales",
        storage_type="S3",
        base_location="s3://warehouse/sales",
        allowed_locations=["s3://warehouse/sales"],
        extra_storage={"endpoint": "http://old-host:9000", "region": "eu-west-1"},
    )

    await _ensure(polaris)

    _, config = polaris.storage_updates[0]
    assert config["allowedLocations"] == ["s3://warehouse/sales"]
    assert config["storageType"] == "S3"


async def test_matching_endpoints_are_left_alone():
    """No entity-version churn on every browse."""
    polaris = FakePolaris()
    await polaris.create_catalog(
        "sales",
        storage_type="S3",
        base_location="s3://warehouse/sales",
        extra_storage={
            "endpoint": settings.s3_endpoint,
            "endpointInternal": settings.s3_endpoint_internal,
        },
    )

    await _ensure(polaris)

    assert polaris.storage_updates == []


async def test_external_backends_are_never_reconciled():
    """An external backend's endpoint comes from the operator's own config, so
    DuckHaven has no authority to rewrite it."""
    polaris = FakePolaris()
    await polaris.create_catalog(
        "acme",
        storage_type="S3",
        base_location="s3://acme-data/duckhaven",
        extra_storage={"roleArn": "arn:aws:iam::1:role/x", "region": "us-east-1"},
    )

    await _ensure(
        polaris,
        name="acme",
        base_location="s3://acme-data/duckhaven",
        extra_storage={"roleArn": "arn:aws:iam::1:role/x", "region": "us-east-1"},
    )

    assert polaris.storage_updates == []


async def test_a_refused_update_does_not_break_browsing():
    """Reconciliation is a self-heal, not a gate: a Polaris that refuses the
    edit must still leave the catalog usable at its recorded endpoint."""
    polaris = FakePolaris()
    await polaris.create_catalog(
        "sales",
        storage_type="S3",
        base_location="s3://warehouse/sales",
        extra_storage={"endpoint": "http://old-host:9000"},
    )

    async def _refuse(*_a, **_k):
        raise PolarisError("simulated refusal")

    polaris.update_catalog_storage = _refuse

    await _ensure(polaris)  # must not raise

    assert "sales" in polaris.granted_catalogs
