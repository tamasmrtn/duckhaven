"""The session credential-vending seam: API-vended Polaris block + scoped staging."""

import uuid
from types import SimpleNamespace

from api.config import settings
from api.services import session_credentials as sc


def _catalog(root_uri, kind="s3", config=None):
    return SimpleNamespace(
        storage_backend=SimpleNamespace(root_uri=root_uri, kind=kind, config=config)
    )


def test_polaris_block_comes_from_api_config():
    block = sc.build_polaris_block()
    assert block == {
        "endpoint": settings.polaris_base_url,
        "client_id": settings.polaris_client_id,
        "client_secret": settings.polaris_client_secret,
    }


def test_staging_uri_scopes_under_catalog_root_and_session():
    session_id = uuid.uuid4()
    uri = sc.staging_uri_for(_catalog("s3://warehouse/analytics/", kind="s3"), session_id)
    assert uri == f"s3://warehouse/analytics/_staging/{session_id}/"
    # The policy is handed exactly this one prefix.
    assert sc.staging_prefixes(uri) == [uri]


def test_staging_uri_for_bundled_object_store_resolves_bucket():
    # object_store's root_uri is a bucket-relative label (empty for a name-only
    # workspace), yet staging must resolve to a real s3:// location under the
    # bundled bucket so it can be presigned.
    session_id = uuid.uuid4()
    uri = sc.staging_uri_for(_catalog("", kind="object_store"), session_id)
    assert uri == f"s3://{settings.s3_bucket}/_staging/{session_id}/"


def test_staging_prefixes_none_without_uri():
    assert sc.staging_prefixes(None) == []
