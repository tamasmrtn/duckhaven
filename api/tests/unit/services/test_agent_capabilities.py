from api.services.agent_capabilities import agent_supports_backend, required_extension


def test_required_extension_mapping():
    assert required_extension("s3") == "httpfs"
    assert required_extension("adls_gen2") == "azure"
    # object_store is MinIO-backed (S3) and so also needs httpfs.
    assert required_extension("object_store") == "httpfs"


def test_object_store_requires_httpfs():
    assert agent_supports_backend({"extensions": ["httpfs"]}, "object_store") is True
    assert agent_supports_backend({"extensions": []}, "object_store") is False
    assert agent_supports_backend(None, "object_store") is False


def test_cloud_backend_requires_extension():
    assert agent_supports_backend({"extensions": ["httpfs"]}, "s3") is True
    assert agent_supports_backend({"extensions": ["iceberg"]}, "s3") is False
    assert agent_supports_backend({"extensions": ["azure"]}, "adls_gen2") is True
    assert agent_supports_backend({"extensions": []}, "adls_gen2") is False
    assert agent_supports_backend(None, "s3") is False
