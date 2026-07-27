"""Compute backends, selected by the ``provider`` string.

A backend knows how to create, destroy, enumerate and locate the *instances* that run
an agent container. It knows nothing about DuckHaven's Agent rows or lifecycle — that
is the service/reaper's job. Keeping the surface this narrow is what lets a second
cloud be added later as one module without a premature Protocol/registry.

``address`` exists because an instance on a private network cannot report its own
reachable address: it is assigned after the container's configuration is fixed, and the
socket it dials home on may arrive translated. Backends that have nothing to add return
``None`` and the socket's peer address is used instead.

Phase 0 ships only ``NullBackend``: a no-op, in-process backend that lets the whole
lifecycle (coalescing, idle reaping, leak reconciliation) be unit-tested without a
cloud. ``azure_aci`` lands in Phase 1 as a sibling module and is wired into
``get_backend`` there.

``capacity`` reports the largest agent the platform will actually run, because only
the backend knows: for a cloud it is the provider's per-instance cap, for a single
host it is the machine itself. Returning ``None`` means "no opinion", and the caller
falls back to a conservative default.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProvisionRequest:
    """Everything a backend needs to start one agent container.

    The agent enrolls with exactly these two env vars (see add-agent docs), so a
    backend just starts ``image`` with ``CONTROL_PLANE_URL`` + ``BOOTSTRAP_TOKEN``.
    ``instance_id`` is chosen by the caller (deterministic from the agent id) so a
    crash between "create" and "record" is reconcilable, never a silent leak.
    """

    instance_id: str
    image: str
    control_plane_url: str
    bootstrap_token: str
    # The container size. None falls back to the backend's configured default.
    cpu: float | None = None
    memory_gb: float | None = None
    # Free-form tags the backend stamps on the instance so the leak sweep can tell
    # DuckHaven-managed instances apart from anything else in the subscription.
    tags: dict[str, str] = field(default_factory=dict)


class NullBackend:
    """A no-op backend that only tracks which instance ids it has "provisioned".

    In-process state (a set) is enough to exercise leak reconciliation and the
    happy path in unit tests. It is intentionally not I9 state — real backends
    reconcile against the actual cloud.
    """

    provider = "null"

    def __init__(self) -> None:
        self._instances: set[str] = set()

    async def provision(self, req: ProvisionRequest) -> str:
        self._instances.add(req.instance_id)
        return req.instance_id

    async def terminate(self, instance_id: str) -> None:
        self._instances.discard(instance_id)

    async def address(self, instance_id: str) -> str | None:
        """No network to report: a null instance is reached over the socket it dialed
        in on, the same as a static agent."""
        return None

    async def status(self, instance_id: str) -> str:
        return "running" if instance_id in self._instances else "gone"

    async def list_managed(self) -> set[str]:
        """Instance ids the backend currently holds (the leak-sweep source)."""
        return set(self._instances)

    async def capacity(self) -> tuple[float, float] | None:
        """No platform to measure, so no opinion on how big an agent may be."""
        return None


# One instance per provider string, built on first use. The null backend is a
# singleton so its in-process set is shared across a test's ensure/reap calls; the
# azure_aci backend is constructed lazily so its SDK import only happens when it is
# actually the configured provider.
_BACKENDS: dict[str, object] = {"null": NullBackend()}


def get_backend(provider: str) -> object:
    """Return the backend for a ``provider`` string.

    Raises ``KeyError`` for an unknown provider — a misconfiguration we want to
    fail loudly, not silently no-op.
    """
    backend = _BACKENDS.get(provider)
    if backend is None:
        if provider == "azure_aci":
            from api.services.compute.azure_aci import AzureAciBackend

            backend = AzureAciBackend()
        elif provider == "docker":
            from api.services.compute.docker_engine import DockerEngineBackend

            backend = DockerEngineBackend()
        else:
            raise KeyError(provider)
        _BACKENDS[provider] = backend
    return backend
