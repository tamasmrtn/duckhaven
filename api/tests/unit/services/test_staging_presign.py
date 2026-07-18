"""Per-backend presigning of SQL-session staging URLs (issue #160).

The cloud SDKs are mocked (as ``test_storage_health`` does) so these run without
real MinIO/AWS/Azure. The integration round-trip lives under
``tests/integration`` (env-gated, bundled MinIO only).
"""

import uuid
from types import SimpleNamespace

import pytest

from api.config import settings
from api.services import staging_presign as sp


def _catalog(kind, root_uri, config=None):
    return SimpleNamespace(
        storage_backend=SimpleNamespace(kind=kind, root_uri=root_uri, config=config)
    )


# ── S3 / MinIO ────────────────────────────────────────────────────────────────


class _FakeS3Client:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803 - boto3 kwarg name
        # Mimics a path-style presigned URL: host is the endpoint, then bucket/key.
        return f"{self.endpoint}/{Params['Bucket']}/{Params['Key']}?op={op}&exp={ExpiresIn}"


class _FakeSts:
    def __init__(self, recorder):
        self.recorder = recorder

    def assume_role(self, **kwargs):
        self.recorder.append(kwargs)
        return {
            "Credentials": {
                "AccessKeyId": "AK-temp",
                "SecretAccessKey": "SK-temp",
                "SessionToken": "TK-temp",
            }
        }


@pytest.fixture
def fake_boto(monkeypatch):
    clients: list[dict] = []
    assume_calls: list[dict] = []

    def _client(service, endpoint_url=None, region_name=None, config=None, **creds):
        if service == "sts":
            return _FakeSts(assume_calls)
        clients.append({"endpoint": endpoint_url, "region": region_name, "creds": creds})
        return _FakeS3Client(endpoint_url)

    import boto3

    monkeypatch.setattr(boto3, "client", _client)
    return SimpleNamespace(clients=clients, assume_calls=assume_calls)


def test_object_store_splits_put_external_get_internal(fake_boto):
    session_id = uuid.uuid4()
    catalog = _catalog("object_store", "")
    files, _ = sp.presign_staging_files(catalog, session_id, ["orders.parquet"], ttl_s=900)

    f = files[0]
    prefix = f"_staging/{session_id}/orders.parquet"
    assert f.key == f"s3://{settings.s3_bucket}/_staging/{session_id}/orders.parquet"
    # The client PUTs to the external endpoint; the agent GETs the internal one.
    assert f.put_url.startswith(f"{settings.s3_endpoint}/{settings.s3_bucket}/{prefix}")
    assert f.get_url.startswith(f"{settings.s3_endpoint_internal}/{settings.s3_bucket}/{prefix}")
    # Two distinct clients (external + internal endpoint), static MinIO creds.
    endpoints = {c["endpoint"] for c in fake_boto.clients}
    assert endpoints == {settings.s3_endpoint, settings.s3_endpoint_internal}
    assert fake_boto.clients[0]["creds"]["aws_access_key_id"] == settings.s3_access_key


def test_object_store_read_prefix_matches_get_url(fake_boto):
    session_id = uuid.uuid4()
    catalog = _catalog("object_store", "")
    files, _ = sp.presign_staging_files(catalog, session_id, ["orders.parquet"], ttl_s=900)
    prefixes = sp.staging_read_prefixes(catalog, session_id)

    assert prefixes == [
        f"{settings.s3_endpoint_internal}/{settings.s3_bucket}/_staging/{session_id}/"
    ]
    # The statement policy admits the get_url because it starts with this prefix.
    assert files[0].get_url.startswith(prefixes[0])


def test_external_s3_assumes_role_and_uses_single_endpoint(fake_boto):
    session_id = uuid.uuid4()
    catalog = _catalog(
        "s3",
        "s3://acme/dh/",
        {"role_arn": "arn:aws:iam::1:role/x", "region": "eu-west-1", "external_id": "xid"},
    )
    files, _ = sp.presign_staging_files(catalog, session_id, ["o.parquet"], ttl_s=600)

    assert fake_boto.assume_calls[0]["RoleArn"] == "arn:aws:iam::1:role/x"
    assert fake_boto.assume_calls[0]["ExternalId"] == "xid"
    # No custom endpoint configured -> regional AWS host, same for put and get.
    host = "https://s3.eu-west-1.amazonaws.com"
    assert files[0].put_url.startswith(f"{host}/acme/dh/_staging/{session_id}/o.parquet")
    assert files[0].get_url.startswith(f"{host}/acme/dh/_staging/{session_id}/o.parquet")
    # Presigned with the assumed-role session token.
    assert fake_boto.clients[0]["creds"]["aws_session_token"] == "TK-temp"


def test_unsupported_kind_raises():
    with pytest.raises(sp.StagingUnavailable):
        sp.presign_staging_files(_catalog("gcs", "gs://b/"), uuid.uuid4(), ["f"], ttl_s=60)


# ── Azure ADLS / Blob ─────────────────────────────────────────────────────────


@pytest.fixture
def fake_azure(monkeypatch):
    perms: list[object] = []

    class _FakeDelegationKey:
        pass

    class _FakeBlobService:
        def __init__(self, account_url, credential=None):
            self.account_url = account_url

        def get_user_delegation_key(self, key_start_time, key_expiry_time):
            return _FakeDelegationKey()

    def _fake_generate_blob_sas(**kwargs):
        perms.append(kwargs["permission"])
        return f"sig=token&perm={kwargs['permission']}"

    import azure.identity
    import azure.storage.blob

    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(azure.storage.blob, "BlobServiceClient", _FakeBlobService)
    monkeypatch.setattr(azure.storage.blob, "generate_blob_sas", _fake_generate_blob_sas)
    return SimpleNamespace(perms=perms)


def test_adls_user_delegation_sas_put_write_get_read(fake_azure):
    session_id = uuid.uuid4()
    catalog = _catalog("adls_gen2", "abfss://cont@acct.dfs.core.windows.net/dh/")
    files, _ = sp.presign_staging_files(catalog, session_id, ["o.parquet"], ttl_s=600)

    f = files[0]
    base = f"https://acct.blob.core.windows.net/cont/dh/_staging/{session_id}/o.parquet"
    assert f.put_url.startswith(f"{base}?")
    assert f.get_url.startswith(f"{base}?")
    # First SAS (put) grants write; second (get) is read-only.
    put_perm, get_perm = fake_azure.perms
    assert put_perm.write is True
    assert get_perm.write is False
    assert get_perm.read is True


def test_adls_read_prefix(fake_azure):
    session_id = uuid.uuid4()
    catalog = _catalog("adls_gen2", "abfss://cont@acct.dfs.core.windows.net/dh/")
    prefixes = sp.staging_read_prefixes(catalog, session_id)
    assert prefixes == [f"https://acct.blob.core.windows.net/cont/dh/_staging/{session_id}/"]
