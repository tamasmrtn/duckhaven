"""Semantic providers, selected by the ``provider`` string.

An adapter translates one producer's own format into the canonical model list
:mod:`api.services.semantic.ingest` accepts. It knows nothing about the store,
authorization or validation, which is what keeps "support another producer" down
to one module.

Deliberately duck-typed with no ``Protocol`` or ABC, mirroring
``services/lineage/providers`` and ``services/compute/backends``: the surface is
one function, and a formal interface over a single function is the kind of
abstraction that costs more than it explains. An adapter is:

    async def models_from_<producer>(payload, *, resolve) -> ProviderModels

``resolve`` is supplied by the caller and turns a producer's idea of a table into
a DuckHaven catalog reference — the adapter never touches the database itself.

Adapters are ``async`` because a producer's format may need a schema the artifact
does not carry. One that never does simply never awaits.

``native`` is a reserved name with no adapter: those definitions are authored
through the API by a person, so accepting them over the import route would let a
client forge human provenance on something a pipeline wrote.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from api.services.lineage.resolve import Skipped
from api.services.semantic.ingest import CanonicalModel


@dataclass
class ProviderModels:
    """What an adapter recovered from one producer's artifact."""

    models: list[CanonicalModel] = field(default_factory=list)
    # Definitions the artifact contained but this adapter could not represent.
    # Reported rather than dropped: a silently-missing metric looks exactly like
    # one nobody ever wrote, and the two need very different responses.
    skipped: list[Skipped] = field(default_factory=list)
    # Every model slug the artifact declares, including ones that ended up empty.
    # Reconciliation is scoped to these rather than to `models`, so a model whose
    # last metric was deleted is still recognised as declared and is not pruned.
    model_slugs: set[str] = field(default_factory=set)


# Registered lazily so importing this package does not drag in every producer's
# parsing code.
_ADAPTERS: dict[str, Callable] = {}


def get_adapter(provider: str) -> Callable:
    """Return the adapter for a ``provider`` string.

    Raises ``KeyError`` for a provider with no adapter, which the router turns
    into a 422 — an unknown producer name is a client mistake worth reporting,
    not something to accept silently.
    """
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        if provider == "duckhaven":
            from api.services.semantic.providers.native import models_from_yaml

            adapter = models_from_yaml
        elif provider == "dbt":
            from api.services.semantic.providers.dbt import models_from_manifest

            adapter = models_from_manifest
        else:
            raise KeyError(provider)
        _ADAPTERS[provider] = adapter
    return adapter
