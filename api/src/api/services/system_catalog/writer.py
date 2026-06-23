"""Writing the system catalog's Iceberg tables from the control plane.

The materializer is the *only* place the control plane writes object storage, and
it writes exactly one DuckHaven-owned catalog via PyIceberg against the Polaris
REST API with access delegation (so storage credentials are short-lived and
vended, never held). Agents still read it like any other attached catalog.

:class:`SystemCatalogWriter` is the seam the materializer depends on;
:class:`IcebergSystemCatalogWriter` is the real PyIceberg implementation and unit
tests substitute an in-memory fake.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import pyarrow as pa

from api.services.system_catalog.constants import SYSTEM_CATALOG_SLUG
from api.services.system_catalog.tables import SystemTable

logger = logging.getLogger(__name__)


@runtime_checkable
class SystemCatalogWriter(Protocol):
    def ensure_table(self, table: SystemTable) -> None: ...

    def append(self, table: SystemTable, rows: list[dict]) -> None: ...

    def overwrite(self, table: SystemTable, rows: list[dict]) -> None: ...


def _to_arrow(table: SystemTable, rows: list[dict]) -> pa.Table:
    if not rows:
        return table.schema.empty_table()
    return pa.Table.from_pylist(rows, schema=table.schema)


class IcebergSystemCatalogWriter:
    """Writes the system catalog's tables to Polaris via PyIceberg.

    Construction is deferred (``_catalog`` lazily built on first use) so importing
    this module never opens a network connection; a Polaris outage surfaces only
    when the materializer actually runs, and is swallowed by its loop.
    """

    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        warehouse: str = SYSTEM_CATALOG_SLUG,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        # The Polaris catalog (REST prefix) to write into. Overridable so tests
        # can target a throwaway catalog instead of the real ``duckhaven``.
        self._warehouse = warehouse
        self._catalog = None

    def _rest_catalog(self):
        if self._catalog is None:
            from pyiceberg.catalog.rest import RestCatalog

            self._catalog = RestCatalog(
                "duckhaven_system",
                **{
                    "uri": f"{self._base_url}/api/catalog",
                    "warehouse": self._warehouse,
                    "credential": f"{self._client_id}:{self._client_secret}",
                    "oauth2-server-uri": f"{self._base_url}/api/catalog/v1/oauth/tokens",
                    "scope": "PRINCIPAL_ROLE:ALL",
                    # Polaris multi-tenant realm header + storage credential vending.
                    "header.Polaris-Realm": self._realm,
                    "header.X-Iceberg-Access-Delegation": "vended-credentials",
                },
            )
        return self._catalog

    def ensure_table(self, table: SystemTable) -> None:
        from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError

        catalog = self._rest_catalog()
        try:
            catalog.create_namespace(table.namespace)
        except NamespaceAlreadyExistsError:
            pass
        try:
            catalog.create_table(table.identifier, schema=table.schema)
        except TableAlreadyExistsError:
            pass

    def append(self, table: SystemTable, rows: list[dict]) -> None:
        if not rows:
            return
        self.ensure_table(table)
        self._rest_catalog().load_table(table.identifier).append(_to_arrow(table, rows))

    def overwrite(self, table: SystemTable, rows: list[dict]) -> None:
        self.ensure_table(table)
        self._rest_catalog().load_table(table.identifier).overwrite(_to_arrow(table, rows))
