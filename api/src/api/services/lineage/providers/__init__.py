"""Lineage providers, selected by the ``provider`` string.

A provider adapter translates one producer's own format into the canonical edge
list :mod:`api.services.lineage.ingest` accepts. It knows nothing about the
store, the graph or authorization, which is what keeps "support another producer"
down to one module.

Deliberately duck-typed with no ``Protocol`` or ABC, mirroring
``services/compute/backends.py``: the surface is one function, and a formal
interface over a single function is the kind of abstraction that costs more than
it explains. An adapter is:

    async def edges_from_<producer>(payload, *, resolve, catalog=None) -> ProviderEdges

``resolve`` is supplied by the caller and turns a producer's idea of an endpoint
into a DuckHaven asset — the adapter never touches the database itself. ``catalog``
is an optional second artifact carrying the producer's own view of each relation's
columns, for producers that publish one.

Adapters are ``async`` because working out column-level detail may need to read a
schema the artifact does not carry. One that never does simply never awaits.

``execution`` is a reserved name with no adapter: that lineage is derived by
DuckHaven from SQL it ran, so accepting it over the import API would let a client
forge provenance. The import router rejects it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from api.services.lineage.ingest import CanonicalEdge
from api.services.lineage.resolve import Skipped


@dataclass
class ProviderEdges:
    """What an adapter recovered from one producer's artifact."""

    edges: list[CanonicalEdge] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    # Every asset the artifact describes as *built by this producer*, including
    # ones that ended up with no edges at all.
    #
    # Reconciliation is scoped to these rather than to the targets of `edges`,
    # and the difference is load-bearing: a model whose last dependency was
    # deleted contributes no edge, so scoping to edge targets would leave its
    # stale edges asserted forever with no way to prune them.
    targets: set[str] = field(default_factory=set)


# Adapters are registered lazily so an import of this package does not drag in
# every producer's parsing code.
_ADAPTERS: dict[str, Callable] = {}


def get_adapter(provider: str) -> Callable:
    """Return the adapter for a ``provider`` string.

    Raises ``KeyError`` for a provider with no adapter, which the router turns
    into a 422 — an unknown producer name is a client mistake worth reporting,
    not something to silently accept.
    """
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        if provider == "dbt":
            from api.services.lineage.providers.dbt import edges_from_manifest

            adapter = edges_from_manifest
        else:
            raise KeyError(provider)
        _ADAPTERS[provider] = adapter
    return adapter
