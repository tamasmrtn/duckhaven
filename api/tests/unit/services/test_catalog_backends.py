"""The catalog-metadata seam: registry, capabilities and error translation.

Polaris behaviour is covered end to end by `routers/test_schemas.py`.
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

    Names only; signatures still need a type checker.
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
    """The mapping stays 1:1 with the app-level handler's status codes."""
    translated = _translate(polaris_exc)
    assert isinstance(translated, expected)
    assert str(translated) == str(polaris_exc)


def test_iceberg_capabilities_are_honest():
    caps = capabilities_for(KIND_ICEBERG_POLARIS)
    # Spark/Trino/Flink/PyIceberg can read these tables, unlike DuckLake.
    assert caps.external_engine_readable is True
    assert caps.supports_storage_migration is True
    assert set(caps.supported_storage_kinds) == {"object_store", "s3", "adls_gen2"}


def test_ducklake_catalog_resolves_to_the_ducklake_backend():
    from api.services.catalog_backends.ducklake import DuckLakeCatalogBackend

    backend = backend_for(_catalog(kind=KIND_DUCKLAKE), polaris=None)
    assert isinstance(backend, DuckLakeCatalogBackend)
    assert backend.kind == KIND_DUCKLAKE


def test_ducklake_capabilities_state_the_trade_off():
    caps = capabilities_for(KIND_DUCKLAKE)
    # The one genuine trade-off: no other engine can open these tables.
    assert caps.external_engine_readable is False
    # Everything else it does at least as well. Relocating is a prefix copy
    # plus one row; maintenance it can actually run.
    assert caps.supports_storage_migration is True
    assert caps.supports_maintenance_apply is True


def test_iceberg_keeps_every_capability_it_had():
    """The whole exercise is additive: nothing DuckLake gained was taken from
    the default kind."""
    caps = capabilities_for(KIND_ICEBERG_POLARIS)
    assert caps.external_engine_readable is True
    assert caps.supports_storage_migration is True
    # The exception, and it is the extension's doing rather than a choice.
    assert caps.supports_maintenance_apply is False
