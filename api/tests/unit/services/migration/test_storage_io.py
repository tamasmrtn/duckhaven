"""Regression coverage for the directory-style listing prefix.

Polaris vends MinIO/S3 STS credentials scoped to an ``s3:prefix`` StringLike
condition of ``<table-location>/*`` (see ``docker exec`` inspection during
manual testing): the request's ``Prefix`` must itself end in "/" to satisfy
that condition, or MinIO denies ``ListObjectsV2`` outright even though
Get/PutObject on the same location succeed. A bare prefix would also risk
sweeping in a sibling table whose name is a superstring (e.g. "users" vs
"users2")."""

from __future__ import annotations

from api.services.migration import storage_io
from api.services.migration.storage_io import StorageContext


class _FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(kwargs)
        return {"Contents": [], "IsTruncated": False}


def test_s3_list_prefix_has_trailing_slash(monkeypatch) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(storage_io, "_s3_client", lambda ctx: fake)

    storage_io.list_objects(
        StorageContext("object_store", {}, {}), "s3://warehouse/new/analytics/users"
    )

    assert len(fake.calls) == 1
    assert fake.calls[0]["Bucket"] == "warehouse"
    assert fake.calls[0]["Prefix"] == "new/analytics/users/"


def test_s3_list_prefix_already_has_trailing_slash(monkeypatch) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(storage_io, "_s3_client", lambda ctx: fake)

    storage_io.list_objects(
        StorageContext("object_store", {}, {}), "s3://warehouse/new/analytics/users/"
    )

    assert fake.calls[0]["Prefix"] == "new/analytics/users/"


class _FakeAdlsClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_blobs(self, *, name_starts_with):
        self.calls.append(name_starts_with)
        return []


def test_adls_list_prefix_has_trailing_slash(monkeypatch) -> None:
    fake = _FakeAdlsClient()
    monkeypatch.setattr(storage_io, "_adls_container", lambda ctx, uri: fake)

    storage_io.list_objects(
        StorageContext("adls_gen2", {}, {}),
        "abfss://c@acct.dfs.core.windows.net/new/analytics/users",
    )

    assert fake.calls == ["new/analytics/users/"]
