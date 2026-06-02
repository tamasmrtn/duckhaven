from api.services.agent_capabilities import agent_supports_backend, required_extension


def test_required_extension_mapping():
    assert required_extension("s3") == "httpfs"
    assert required_extension("adls_gen2") == "azure"
    # local_fs/nas are MinIO-backed (S3) and so also need httpfs.
    assert required_extension("local_fs") == "httpfs"
    assert required_extension("nas") == "httpfs"


def test_local_and_nas_require_httpfs():
    assert agent_supports_backend({"extensions": ["httpfs"]}, "local_fs") is True
    assert agent_supports_backend({"extensions": []}, "local_fs") is False
    assert agent_supports_backend({"extensions": ["httpfs"]}, "nas") is True
    assert agent_supports_backend(None, "nas") is False


def test_cloud_backend_requires_extension():
    assert agent_supports_backend({"extensions": ["httpfs"]}, "s3") is True
    assert agent_supports_backend({"extensions": ["iceberg"]}, "s3") is False
    assert agent_supports_backend({"extensions": ["azure"]}, "adls_gen2") is True
    assert agent_supports_backend({"extensions": []}, "adls_gen2") is False
    assert agent_supports_backend(None, "s3") is False
