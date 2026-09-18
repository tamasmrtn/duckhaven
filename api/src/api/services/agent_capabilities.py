"""Required-extension mapping and the dispatch-time compatibility check (G-D17-b).

An agent must satisfy two independent axes to serve a catalog: its **catalog
kind** (the metastore and table format) and its **storage backend** (where the
bytes live). The storage half is mirrored in
`web/src/components/app/AgentPicker.tsx`; the kind half is enforced only here.
"""

# Every backend is object storage now; object_store is the bundled S3 store and
# so also needs httpfs.
_BACKEND_EXTENSION: dict[str, str] = {
    "object_store": "httpfs",
    "s3": "httpfs",
    "adls_gen2": "azure",
}

# Extensions gated at dispatch, per catalog kind. `iceberg_polaris` is empty on
# purpose: gating it now would refuse agents with a stale advertised set.
# `postgres_scanner` is what the agent advertises for the `postgres` extension.
_CATALOG_KIND_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "iceberg_polaris": (),
    "ducklake": ("ducklake", "postgres_scanner"),
}


def required_extension(backend_kind: str) -> str | None:
    """The DuckDB extension an agent must have loaded to serve this backend."""
    return _BACKEND_EXTENSION.get(backend_kind)


def required_catalog_extensions(catalog_kind: str) -> tuple[str, ...]:
    """The DuckDB extensions gated at dispatch for this catalog kind.

    An unknown kind maps to no requirement, since the control plane decides what
    it can provision.
    """
    return _CATALOG_KIND_EXTENSIONS.get(catalog_kind, ())


def _loaded(capabilities: dict | None) -> list[str]:
    return (capabilities or {}).get("extensions") or []


def agent_supports_backend(capabilities: dict | None, backend_kind: str) -> bool:
    ext = required_extension(backend_kind)
    if ext is None:
        return True
    return ext in _loaded(capabilities)


def agent_supports_catalog_kind(capabilities: dict | None, catalog_kind: str) -> bool:
    extensions = _loaded(capabilities)
    return all(ext in extensions for ext in required_catalog_extensions(catalog_kind))


def agent_supports_catalog(capabilities: dict | None, catalog_kind: str, backend_kind: str) -> bool:
    """Whether an agent can serve a catalog of this kind on this backend."""
    return agent_supports_catalog_kind(capabilities, catalog_kind) and agent_supports_backend(
        capabilities, backend_kind
    )


def missing_extension(capabilities: dict | None, catalog_kind: str, backend_kind: str) -> str:
    """The first extension this agent lacks, catalog kind first.

    Returns "" when nothing is missing, which callers only reach after
    ``agent_supports_catalog`` said no.
    """
    loaded = _loaded(capabilities)
    for ext in required_catalog_extensions(catalog_kind):
        if ext not in loaded:
            return ext
    backend_ext = required_extension(backend_kind)
    if backend_ext is not None and backend_ext not in loaded:
        return backend_ext
    return ""


def agent_supports_feature(capabilities: dict | None, feature: str) -> bool:
    """Whether an agent advertises a control-plane protocol feature.

    Absent for agents older than the feature, which is exactly what "does not
    support it" means — so behavior gated on this degrades instead of breaking
    when the API is upgraded ahead of its agents.
    """
    return feature in ((capabilities or {}).get("protocol_features") or [])
