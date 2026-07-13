"""The session credential-vending seam: API-vended Polaris block + scoped staging."""

import uuid
from types import SimpleNamespace

from api.config import settings
from api.services import session_credentials as sc


def _catalog(root_uri):
    return SimpleNamespace(storage_backend=SimpleNamespace(root_uri=root_uri))


def test_polaris_block_comes_from_api_config():
    block = sc.build_polaris_block()
    assert block == {
        "endpoint": settings.polaris_base_url,
        "client_id": settings.polaris_client_id,
        "client_secret": settings.polaris_client_secret,
    }


def test_staging_uri_scopes_under_catalog_root_and_session():
    session_id = uuid.uuid4()
    uri = sc.staging_uri_for(_catalog("s3://warehouse/analytics/"), session_id)
    assert uri == f"s3://warehouse/analytics/_staging/{session_id}/"
    # The policy is handed exactly this one prefix.
    assert sc.staging_prefixes(uri) == [uri]


def test_staging_uri_is_none_without_root():
    assert sc.staging_uri_for(_catalog(""), uuid.uuid4()) is None
    assert sc.staging_prefixes(None) == []
