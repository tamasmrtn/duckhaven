from api.services.agent_capabilities import (
    agent_supports_backend,
    agent_supports_catalog,
    agent_supports_catalog_kind,
    required_catalog_extensions,
    required_extension,
)


def test_required_extension_mapping():
    assert required_extension("s3") == "httpfs"
    assert required_extension("adls_gen2") == "azure"
    # object_store is the bundled S3 store, so it needs httpfs too.
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


def test_catalog_kind_extension_mapping():
    assert required_catalog_extensions("ducklake") == ("ducklake", "postgres_scanner")
    # Iceberg is deliberately ungated (see the mapping's comment).
    assert required_catalog_extensions("iceberg_polaris") == ()
    # An unknown kind requires nothing.
    assert required_catalog_extensions("something_new") == ()


def test_ducklake_needs_the_postgres_scanner_spelling():
    """DuckDB installs `postgres` but advertises `postgres_scanner`; matching the
    install name would refuse every DuckLake dispatch."""
    advertised = {"extensions": ["ducklake", "postgres_scanner", "httpfs"]}
    assert agent_supports_catalog_kind(advertised, "ducklake") is True
    install_name_only = {"extensions": ["ducklake", "postgres", "httpfs"]}
    assert agent_supports_catalog_kind(install_name_only, "ducklake") is False


def test_ducklake_needs_both_of_its_extensions():
    assert agent_supports_catalog_kind({"extensions": ["ducklake"]}, "ducklake") is False
    assert agent_supports_catalog_kind({"extensions": ["postgres_scanner"]}, "ducklake") is False
    assert agent_supports_catalog_kind(None, "ducklake") is False


def test_an_iceberg_only_agent_still_serves_iceberg():
    """Adding the catalog-kind axis must not narrow the storage axis."""
    assert agent_supports_catalog({"extensions": ["httpfs"]}, "iceberg_polaris", "object_store")
    legacy = {"extensions": ["httpfs", "azure", "iceberg"]}
    assert agent_supports_catalog(legacy, "iceberg_polaris", "object_store") is True
    assert agent_supports_catalog(legacy, "iceberg_polaris", "s3") is True
    assert agent_supports_catalog(legacy, "iceberg_polaris", "adls_gen2") is True
    # It cannot serve DuckLake, which is the point of the second axis.
    assert agent_supports_catalog(legacy, "ducklake", "object_store") is False


def test_both_axes_must_pass():
    """A DuckLake catalog on ADLS needs the kind's extensions and azure."""
    no_azure = {"extensions": ["ducklake", "postgres_scanner", "httpfs"]}
    assert agent_supports_catalog(no_azure, "ducklake", "object_store") is True
    assert agent_supports_catalog(no_azure, "ducklake", "adls_gen2") is False

    full = {"extensions": ["ducklake", "postgres_scanner", "httpfs", "azure"]}
    assert agent_supports_catalog(full, "ducklake", "adls_gen2") is True
