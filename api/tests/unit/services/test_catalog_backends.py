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


def test_both_backends_implement_every_protocol_method():
    """Catches a method added to the Protocol without an implementation.

    Compares the method *names* only — that is all a Protocol can be checked
    against without a type checker, so a changed signature or a sync/async
    mismatch still gets through here.
    """
    from api.services.catalog_backends.ducklake import DuckLakeCatalogBackend

    required = {
        name
        for name in dir(CatalogBackend)
        if not name.startswith("_") and callable(getattr(CatalogBackend, name, None))
    }
    for impl in (PolarisCatalogBackend, DuckLakeCatalogBackend):
        missing = required - {n for n in dir(impl) if not n.startswith("_")}
        assert not missing, f"{impl.__name__} is missing {sorted(missing)}"


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


def test_ducklake_catalog_resolves_to_the_ducklake_backend():
    from api.services.catalog_backends.ducklake import DuckLakeCatalogBackend

    backend = backend_for(_catalog(kind=KIND_DUCKLAKE), polaris=None)
    assert isinstance(backend, DuckLakeCatalogBackend)
    assert backend.kind == KIND_DUCKLAKE


def test_ducklake_capabilities_state_the_trade_off():
    caps = capabilities_for(KIND_DUCKLAKE)
    # A DuckLake snapshot is a commit against the catalog, not one table.
    assert caps.snapshot_granularity == "catalog"
    # The cost: no other engine can open these tables. This is what the create
    # dialog has to tell the user before they choose.
    assert caps.external_engine_readable is False
    # The win: DuckDB can actually run this kind's maintenance.
    assert caps.maintenance_executable is True
    # Iceberg's path-rewriting migration engine does not apply here.
    assert caps.supports_storage_migration is False
