from api.config import settings
from api.services.workspace import polaris_storage


def test_object_store_empty_root_is_bucket_root():
    storage_type, base, extra = polaris_storage("object_store", "")
    assert storage_type == "S3"
    # The bundled-MinIO bucket root; per-workspace isolation is added later by
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


def test_s3_kind_is_external_unchanged():
    storage_type, base, extra = polaris_storage("s3", "s3://my-bucket/duckhaven/")
    assert storage_type == "S3"
    assert base == "s3://my-bucket/duckhaven"
    # External operator-owned store: no bundled-MinIO endpoint injected.
    assert extra is None


def test_adls_kind_is_external_unchanged():
    storage_type, base, extra = polaris_storage(
        "adls_gen2", "abfss://c@acct.dfs.core.windows.net/duckhaven/"
    )
    assert storage_type == "AZURE"
    assert base == "abfss://c@acct.dfs.core.windows.net/duckhaven"
    assert extra is None
