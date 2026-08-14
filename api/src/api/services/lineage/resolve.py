"""Turning a producer's idea of an endpoint into a DuckHaven asset.

Shared by every importer so they agree on what "the table called X in database Y"
means, and so an unresolvable reference is reported the same way regardless of
which producer sent it.

The rule for an unknown catalog is asymmetric on purpose:

- a **source** the producer describes as living in an unknown system is exactly
  the legitimate external-asset case (a source table in a database DuckHaven does
  not manage), so it becomes an external node;
- a **target** in an unknown catalog is a mistake — DuckHaven cannot be building
  a table it has never heard of — so it is skipped and reported.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.models.catalog import Catalog
from api.services.lineage.keys import AssetRef, external_ref, internal_ref


@dataclass(frozen=True)
class Skipped:
    """An endpoint that could not be resolved, and why."""

    ref: str
    reason: str


class Resolver:
    """Resolves imported endpoints against the catalogs a workspace attaches."""

    def __init__(self, catalogs: list[Catalog]) -> None:
        self._by_slug = {c.slug: c for c in catalogs}

    def resolve(
        self,
        *,
        catalog: str | None,
        system: str | None,
        schema: str,
        table: str,
        allow_external: bool,
    ) -> tuple[AssetRef | None, Skipped | None]:
        """One endpoint, or the reason it could not be used."""
        label = f"{catalog or system or '?'}.{schema}.{table}"
        if not schema or not table:
            return None, Skipped(ref=label, reason="incomplete_reference")

        if system and not catalog:
            return external_ref(system, schema, table), None

        if catalog is None:
            return None, Skipped(ref=label, reason="no_catalog_or_system")

        found = self._by_slug.get(catalog)
        if found is not None:
            return internal_ref(found.id, schema, table), None

        if allow_external:
            # A source in a system DuckHaven does not manage: keep it, named by
            # the producer, so the imported graph does not lose its roots.
            return external_ref(catalog, schema, table), None
        return None, Skipped(ref=label, reason="unknown_catalog")
