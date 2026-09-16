"""Credential vending for DuckLake catalogs.

An Iceberg catalog gets its storage credentials from Polaris at ATTACH time, so
the control plane vends nothing. DuckLake has no vendor — both the catalog
database credential and the object-store credential are minted here and travel
in the dispatch payload. These tests pin what is minted, and what it is scoped
to.
"""

from __future__ import annotations

import pytest

from api.config import settings
from api.models.catalog import KIND_DUCKLAKE, Catalog
from api.models.storage_backend import StorageBackend
from api.services.session_credentials import (
    _duckdb_endpoint,
    build_ducklake_meta_block,
    build_storage_block,
    ducklake_data_path,
)


def _catalog(slug: str, backend: StorageBackend) -> Catalog:
    cat = Catalog(slug=slug, name=slug, kind=KIND_DUCKLAKE, metadata_schema=f"cat_{slug}")
    cat.storage_backend = backend
    return cat


def _bundled() -> StorageBackend:
    return StorageBackend(kind="object_store", name="bundled", root_uri="")


def test_data_path_is_scoped_per_catalog():
    """Two catalogs sharing a backend must never collide, the same way
    ensure_polaris_catalog scopes an Iceberg catalog's base location."""
    backend = _bundled()
    assert ducklake_data_path(_catalog("raw", backend)) == f"s3://{settings.s3_bucket}/raw/"
    assert ducklake_data_path(_catalog("curated", backend)) == f"s3://{settings.s3_bucket}/curated/"


def test_data_path_resolves_the_bundled_prefix_label():
    """The bundled backend's root_uri is a bucket-relative label, not a URI, so
    it has to go through polaris_storage to become a real location."""
    backend = StorageBackend(kind="object_store", name="bundled", root_uri="lake")
    assert ducklake_data_path(_catalog("raw", backend)) == f"s3://{settings.s3_bucket}/lake/raw/"


def test_meta_block_vends_the_restricted_role_not_the_owner():
    """Handing agents the owner credential would give them the control-plane
    database. The two logins exist precisely to prevent that."""
    original = settings.ducklake_agent_user
    settings.ducklake_agent_user = "ducklake_agent"
    try:
        block = build_ducklake_meta_block()
        assert block["user"] == "ducklake_agent"
        assert block["database"] == settings.ducklake_agent_database
        assert block["user"] != "duckhaven"
        # It must not leak the owner connection string.
        assert settings.ducklake_database_url not in str(block)
    finally:
        settings.ducklake_agent_user = original


def test_bundled_storage_block_is_scoped_to_the_catalog_prefix():
    backend = _bundled()
    cat = _catalog("raw", backend)
    path = ducklake_data_path(cat)
    block = build_storage_block(backend, path)
    assert block["type"] == "s3"
    assert block["scope"] == path
    # Path-style addressing: the bundled store is not a vhost-style S3.
    assert block["url_style"] == "path"
    assert block["key_id"] == settings.s3_access_key


def test_bundled_endpoint_is_stripped_of_its_scheme():
    """DuckDB's S3 secret wants host[:port]; passing a URL makes it unreachable."""
    original = settings.s3_endpoint
    settings.s3_endpoint = "http://objectstore:9000"
    try:
        block = build_storage_block(_bundled(), "s3://warehouse/raw/")
        assert block["endpoint"] == "objectstore:9000"
        assert block["use_ssl"] is False
    finally:
        settings.s3_endpoint = original


def test_https_endpoint_turns_ssl_on():
    original = settings.s3_endpoint
    settings.s3_endpoint = "https://store.example.com"
    try:
        block = build_storage_block(_bundled(), "s3://warehouse/raw/")
        assert block["endpoint"] == "store.example.com"
        assert block["use_ssl"] is True
    finally:
        settings.s3_endpoint = original


def test_external_s3_assumes_the_backends_role(monkeypatch):
    """External S3 keeps genuinely short-lived credentials — I7 preserved — by
    reusing the same assume-role path the staging presigner uses."""
    captured: dict = {}

    class _FakeSts:
        def assume_role(self, **kwargs):
            captured.update(kwargs)
            return {
                "Credentials": {
                    "AccessKeyId": "ASIA...",
                    "SecretAccessKey": "shh",
                    "SessionToken": "tok",
                }
            }

    import boto3

    monkeypatch.setattr(boto3, "client", lambda name, *a, **k: _FakeSts())

    backend = StorageBackend(
        kind="s3",
        name="corp",
        root_uri="s3://corp-bucket/lake",
        config={"role_arn": "arn:aws:iam::1:role/dh", "external_id": "xid", "region": "eu-west-1"},
    )
    block = build_storage_block(backend, "s3://corp-bucket/lake/raw/")
    assert captured["RoleArn"] == "arn:aws:iam::1:role/dh"
    assert captured["ExternalId"] == "xid"
    assert captured["DurationSeconds"] == 3600
    assert block["session_token"] == "tok"
    assert block["region"] == "eu-west-1"
    assert block["scope"] == "s3://corp-bucket/lake/raw/"
    # Default S3 addressing unless the backend asked for path style.
    assert block["url_style"] == "vhost"


def test_external_s3_honours_path_style_access(monkeypatch):
    import boto3

    monkeypatch.setattr(
        boto3,
        "client",
        lambda *a, **k: type(
            "S",
            (),
            {
                "assume_role": lambda self, **kw: {
                    "Credentials": {
                        "AccessKeyId": "a",
                        "SecretAccessKey": "b",
                        "SessionToken": "c",
                    }
                }
            },
        )(),
    )
    backend = StorageBackend(
        kind="s3",
        name="minio",
        root_uri="s3://b/lake",
        config={"role_arn": "arn:x", "region": "us-east-1", "path_style_access": True},
    )
    assert build_storage_block(backend, "s3://b/lake/raw/")["url_style"] == "path"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://objectstore:9000", ("objectstore:9000", False)),
        ("https://store.example.com", ("store.example.com", True)),
        ("http://localhost:4566", ("localhost:4566", False)),
        # Already bare — left alone rather than mangled.
        ("objectstore:9000", ("objectstore:9000", False)),
        # Empty means real AWS, which is HTTPS.
        ("", ("", True)),
    ],
)
def test_endpoint_is_reduced_to_what_duckdb_wants(url, expected):
    """DuckDB's S3 secret takes a bare host, not a URL. Passing a URL through
    makes it request `https://http://host/...` — which is exactly what an
    external S3 backend with a custom endpoint used to do, until a run against
    LocalStack surfaced it."""
    assert _duckdb_endpoint(url) == expected


def test_external_s3_with_a_custom_endpoint_is_usable(monkeypatch):
    """The regression. Any S3-compatible store, VPC endpoint or test double
    carries a custom endpoint, and it must reach DuckDB in a form it accepts."""
    import boto3

    monkeypatch.setattr(
        boto3,
        "client",
        lambda *a, **k: type(
            "S",
            (),
            {
                "assume_role": lambda self, **kw: {
                    "Credentials": {
                        "AccessKeyId": "a",
                        "SecretAccessKey": "b",
                        "SessionToken": "c",
                    }
                }
            },
        )(),
    )
    backend = StorageBackend(
        kind="s3",
        name="localstack",
        root_uri="s3://bucket/lake",
        config={
            "role_arn": "arn:x",
            "region": "us-east-1",
            "endpoint": "http://localhost:4566",
            "path_style_access": True,
        },
    )
    block = build_storage_block(backend, "s3://bucket/lake/raw/")
    assert block["endpoint"] == "localhost:4566"
    assert block["use_ssl"] is False
    assert block["url_style"] == "path"


def test_real_aws_s3_stays_on_https(monkeypatch):
    """No endpoint means AWS, and downgrading that to plain HTTP would be a
    silent security regression."""
    import boto3

    class _Sts:
        def assume_role(self, **kw):
            return {
                "Credentials": {
                    "AccessKeyId": "a",
                    "SecretAccessKey": "b",
                    "SessionToken": "c",
                }
            }

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Sts())
    if True:
        backend = StorageBackend(
            kind="s3",
            name="aws",
            root_uri="s3://bucket/lake",
            config={"role_arn": "arn:x", "region": "eu-west-1"},
        )
        block = build_storage_block(backend, "s3://bucket/lake/raw/")
        assert block["endpoint"] == ""
        assert block["use_ssl"] is True
