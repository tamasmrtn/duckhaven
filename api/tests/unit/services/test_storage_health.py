import uuid
from unittest.mock import AsyncMock

import pytest

from api.models.storage_backend import StorageBackend
from api.services import storage_health
from api.services.polaris import PolarisBadRequestError


def _backend(kind: str, config: dict | None) -> StorageBackend:
    return StorageBackend(
        id=uuid.uuid4(),
        kind=kind,
        name=f"{kind}-backend",
        root_uri="s3://acme-data/duckhaven/" if kind == "s3" else "abfss://c@a.dfs/duckhaven/",
        config=config,
        created_by=uuid.uuid4(),
    )


def _fake_polaris() -> AsyncMock:
    polaris = AsyncMock()
    polaris.load_table_with_credentials.return_value = {
        "config": {"s3.access-key-id": "AK", "s3.secret-access-key": "SK"},
        "metadata": {"location": "s3://acme-data/duckhaven/dhhealthXYZ/dh_health/probe"},
    }
    return polaris


async def test_object_store_passes_without_polaris():
    polaris = AsyncMock()
    result = await storage_health.validate_backend(polaris, _backend("object_store", None))
    assert result.valid is True
    polaris.create_catalog.assert_not_called()


async def test_s3_valid_lists_and_cleans_up(monkeypatch):
    polaris = _fake_polaris()
    monkeypatch.setattr(storage_health, "_list_prefix", lambda *a, **k: 3)
    backend = _backend("s3", {"role_arn": "arn:aws:iam::1:role/x", "region": "us-east-1"})

    result = await storage_health.validate_backend(polaris, backend)

    assert result.valid is True
    assert "3 object" in result.detail
    polaris.create_catalog.assert_awaited_once()
    polaris.delete_catalog.assert_awaited_once()
    # The probe catalog must be scoped under a unique sub-prefix, not the bare
    # backend root, so its allowedLocations never overlap an existing catalog.
    base = polaris.create_catalog.await_args.kwargs["base_location"]
    assert base.startswith("s3://acme-data/duckhaven/")
    assert base != "s3://acme-data/duckhaven"


async def test_empty_exception_message_falls_back_to_type():
    """A failure whose str() is blank still yields a non-empty detail."""
    assert storage_health._short(RuntimeError("")) == "RuntimeError"


async def test_polaris_rejects_config_is_invalid(monkeypatch):
    polaris = _fake_polaris()
    polaris.create_catalog.side_effect = PolarisBadRequestError("invalid roleArn")
    backend = _backend("s3", {"role_arn": "bad", "region": "us-east-1"})

    result = await storage_health.validate_backend(polaris, backend)

    assert result.valid is False
    assert "Polaris rejected" in result.detail


async def test_list_failure_is_invalid_and_cleans_up(monkeypatch):
    polaris = _fake_polaris()

    def _boom(*a, **k):
        raise RuntimeError("access denied")

    monkeypatch.setattr(storage_health, "_list_prefix", _boom)
    backend = _backend("s3", {"role_arn": "arn:aws:iam::1:role/x", "region": "us-east-1"})

    result = await storage_health.validate_backend(polaris, backend)

    assert result.valid is False
    assert "access denied" in result.detail
    # Cleanup runs even when the probe fails.
    polaris.delete_catalog.assert_awaited_once()


def test_list_s3_passes_vended_creds(monkeypatch):
    captured = {}

    class _FakeS3:
        def list_objects_v2(self, **kwargs):
            captured.update(kwargs)
            return {"KeyCount": 2}

    def _fake_client(service, **kwargs):
        captured["client_kwargs"] = kwargs
        return _FakeS3()

    import boto3

    monkeypatch.setattr(boto3, "client", _fake_client)
    creds = {
        "s3.access-key-id": "AK",
        "s3.secret-access-key": "SK",
        "s3.session-token": "TK",
        "client.region": "eu-west-1",
    }
    count = storage_health._list_s3("s3://bucket/base/probe", creds, {})

    assert count == 2
    assert captured["Bucket"] == "bucket"
    assert captured["Prefix"] == "base/probe"
    assert captured["client_kwargs"]["aws_session_token"] == "TK"
    assert captured["client_kwargs"]["region_name"] == "eu-west-1"


def test_list_s3_falls_back_to_config_endpoint(monkeypatch):
    captured = {}

    class _FakeS3:
        def list_objects_v2(self, **kwargs):
            return {"KeyCount": 0}

    def _fake_client(service, **kwargs):
        captured["client_kwargs"] = kwargs
        return _FakeS3()

    import boto3

    monkeypatch.setattr(boto3, "client", _fake_client)
    # Vended creds omit endpoint/region; the backend config supplies them.
    storage_health._list_s3(
        "s3://bucket/probe",
        {"s3.access-key-id": "AK", "s3.secret-access-key": "SK"},
        {"endpoint": "http://minio:9000", "region": "us-east-1"},
    )

    assert captured["client_kwargs"]["endpoint_url"] == "http://minio:9000"
    assert captured["client_kwargs"]["region_name"] == "us-east-1"


def test_list_adls_requires_sas():
    with pytest.raises(ValueError, match="SAS token"):
        storage_health._list_adls("abfss://c@a.dfs.core.windows.net/p", {})
