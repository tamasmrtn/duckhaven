"""Backend → required DuckDB extension mapping and the dispatch-time
compatibility check (G-D17-b).

Mirrors the client-side check in `web/src/components/app/AgentPicker.tsx`
so a non-web client cannot dispatch a workspace to an agent that lacks the
extension its storage backend requires.
"""

# Every backend is object storage now: object_store is backed by the bundled
# MinIO (S3) and so also needs httpfs.
_BACKEND_EXTENSION: dict[str, str] = {
    "object_store": "httpfs",
    "s3": "httpfs",
    "adls_gen2": "azure",
}


def required_extension(backend_kind: str) -> str | None:
    """The DuckDB extension an agent must have loaded to serve this backend."""
    return _BACKEND_EXTENSION.get(backend_kind)


def agent_supports_backend(capabilities: dict | None, backend_kind: str) -> bool:
    ext = required_extension(backend_kind)
    if ext is None:
        return True
    extensions = (capabilities or {}).get("extensions") or []
    return ext in extensions
