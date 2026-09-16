"""Required-extension mapping and the dispatch-time compatibility check (G-D17-b).

Two independent axes, both of which an agent must satisfy to serve a catalog:

- its **catalog kind** — the metastore the agent has to talk to, and the table
  format it has to read (``iceberg``, or ``ducklake`` + its Postgres client);
- its **storage backend** — the object store the bytes live in.

They are checked separately because they vary separately: a DuckLake catalog on
ADLS needs ``ducklake`` + ``postgres_scanner`` + ``azure``, and an Iceberg
catalog on the same backend needs ``iceberg`` + ``azure``.

The storage-backend half is mirrored client-side in
`web/src/components/app/AgentPicker.tsx` so the picker greys out an agent the
API would refuse. The catalog-kind half is enforced here only — the picker does
not yet know about catalog kinds.
"""

# Every backend is object storage now: object_store is backed by the
# bundled object store (S3) and so also needs httpfs.
_BACKEND_EXTENSION: dict[str, str] = {
    "object_store": "httpfs",
    "s3": "httpfs",
    "adls_gen2": "azure",
}

# Extensions gated at dispatch, per catalog kind.
#
# `iceberg_polaris` is empty on purpose: that requirement has never been gated,
# and enforcing it now would refuse agents with a stale advertised set. Separate
# change.
#
# `postgres_scanner`, not `postgres`: DuckDB installs the extension under the
# latter name and advertises it under the former, and this matches what an agent
# advertises. Verified on DuckDB 1.5.5.
_CATALOG_KIND_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "iceberg_polaris": (),
    "ducklake": ("ducklake", "postgres_scanner"),
}


def required_extension(backend_kind: str) -> str | None:
    """The DuckDB extension an agent must have loaded to serve this backend."""
    return _BACKEND_EXTENSION.get(backend_kind)


def required_catalog_extensions(catalog_kind: str) -> tuple[str, ...]:
    """The DuckDB extensions gated at dispatch for this catalog kind.

    An unknown kind maps to no requirement rather than an error: the control
    plane decides what it can provision, and a capability check is the wrong
    place to discover it disagrees with the database.
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
    """The extension to name in an "agent is incompatible" message.

    The first one this agent lacks, checking the catalog kind before the storage
    backend so the message points at the more surprising requirement. Returns
    "" when nothing is missing, which callers only reach by asking after
    ``agent_supports_catalog`` already said no.
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
