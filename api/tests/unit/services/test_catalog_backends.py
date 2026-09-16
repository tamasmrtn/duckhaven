"""The catalog-metadata seam: registry, capabilities and error translation.

The behaviour of the Polaris backend itself is covered by the existing router
suite (`routers/test_schemas.py`), which now exercises it end to end. What is
tested here is the seam's own contract — that a kind resolves to a backend, that
capabilities describe the kind honestly, and that a metastore failure keeps the
HTTP status it had before the seam existed.
"""

from __future__ import annotations

import pytest

from api.models.catalog import KIND_DUCKLAKE, KIND_ICEBERG_POLARIS, Catalog
from api.services.catalog_backends import (
    CatalogBackend,
    CatalogBackendBadRequest,
    CatalogBackendConflict,
    CatalogBackendNotFound,
    CatalogBackendUnavailable,
    backend_for,
    capabilities_for,
)
from api.services.catalog_backends.polaris import PolarisCatalogBackend, _translate
from api.services.polaris import (
    PolarisBadRequestError,
    PolarisConflictError,
    PolarisError,
    PolarisNotFoundError,
    PolarisServerError,
)


def _catalog(kind: str = KIND_ICEBERG_POLARIS) -> Catalog:
    return Catalog(slug="raw", name="raw", kind=kind, polaris_name="raw")


def test_iceberg_catalog_resolves_to_the_polaris_backend():
    backend = backend_for(_catalog(), polaris=object())
    assert isinstance(backend, PolarisCatalogBackend)
    assert backend.kind == KIND_ICEBERG_POLARIS


def test_polaris_backend_satisfies_the_protocol():
    """A runtime check, so a method added to the Protocol without an
    implementation fails here rather than at the first request that needs it."""
    assert isinstance(PolarisCatalogBackend(object()), CatalogBackend)


def test_iceberg_catalog_without_a_client_is_refused():
    with pytest.raises(CatalogBackendUnavailable, match="Polaris client"):
        backend_for(_catalog(), polaris=None)


def test_unknown_kind_is_refused_by_name():
    with pytest.raises(CatalogBackendUnavailable, match="something_new"):
        backend_for(_catalog(kind="something_new"), polaris=object())
    with pytest.raises(CatalogBackendUnavailable):
        capabilities_for("something_new")


@pytest.mark.parametrize(
    ("polaris_exc", "expected"),
    [
        (PolarisNotFoundError("gone"), CatalogBackendNotFound),
        (PolarisBadRequestError("bad"), CatalogBackendBadRequest),
        (PolarisConflictError("dupe"), CatalogBackendConflict),
        (PolarisServerError("boom"), CatalogBackendUnavailable),
        (PolarisError("generic"), CatalogBackendUnavailable),
    ],
)
def test_polaris_errors_translate_one_to_one(polaris_exc, expected):
    """The mapping has to stay 1:1 with the app-level handler's status codes, or
    a Polaris failure silently changes the HTTP response it used to produce."""
    translated = _translate(polaris_exc)
    assert isinstance(translated, expected)
    assert str(translated) == str(polaris_exc)


def test_iceberg_capabilities_are_honest():
    caps = capabilities_for(KIND_ICEBERG_POLARIS)
    # An Iceberg snapshot belongs to one table.
    assert caps.snapshot_granularity == "table"
    # Spark/Trino/Flink/PyIceberg can read these tables — the reason Iceberg is
    # the default kind.
    assert caps.external_engine_readable is True
    # DuckDB's iceberg extension cannot run compaction or snapshot expiry, which
    # is why the maintenance advisor recommends rather than applies.
    assert caps.maintenance_executable is False
    assert caps.supports_storage_migration is True
    assert set(caps.supported_storage_kinds) == {"object_store", "s3", "adls_gen2"}


def test_ducklake_is_not_servable_yet():
    """Until the DuckLake backend lands, its kind resolves to a clear refusal
    rather than an ImportError."""
    with pytest.raises(CatalogBackendUnavailable, match=KIND_DUCKLAKE):
        backend_for(_catalog(kind=KIND_DUCKLAKE), polaris=object())
