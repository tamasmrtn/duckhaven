from api.config import settings
from api.services.workspace import polaris_storage


def test_local_fs_routes_to_bundled_minio():
    storage_type, base, extra = polaris_storage("local_fs", "file:///var/duckhaven/data")
    assert storage_type == "S3"
    # root_uri is treated as a prefix label under the bundled bucket.
    assert base == f"s3://{settings.s3_bucket}/var/duckhaven/data"
    assert extra == {
        "endpoint": settings.s3_endpoint,
        "endpointInternal": settings.s3_endpoint_internal,
        "pathStyleAccess": True,
        "region": settings.s3_region,
    }


def test_nas_routes_to_bundled_minio():
    storage_type, base, extra = polaris_storage("nas", "/mnt/nas01/")
    assert storage_type == "S3"
    assert base == f"s3://{settings.s3_bucket}/mnt/nas01"
    assert extra is not None and extra["endpoint"] == settings.s3_endpoint


def test_local_fs_empty_prefix_is_bucket_root():
    _, base, _ = polaris_storage("local_fs", "/")
    assert base == f"s3://{settings.s3_bucket}"


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
