"""Docker Engine backend, for a single-host deployment.

Provisions each elastic agent as a container on one Docker host — the homelab
counterpart to ``azure_aci``. The agent image, the enrollment contract
(``CONTROL_PLANE_URL`` + ``BOOTSTRAP_TOKEN``) and the lifecycle are identical; only
the thing that creates the container differs.

The daemon is reached over TCP rather than a mounted socket, so the API container
holds no socket file. In the shipped topology that endpoint is a
``docker-socket-proxy`` which allows only the container and image calls below. Read
``docs/deployment/homelab-elastic-setup.md`` before deploying it: the proxy filters
paths and methods, **not** request bodies, so it narrows the API's reach without
making container creation unprivileged.

The docker SDK is synchronous, so every call is offloaded to a thread to keep the
event loop free, and imports are lazy so this module (and ``get_backend``) loads
without ``docker`` installed — only real provisioning needs it.

Containers are named by ``instance_id`` (deterministic, from the agent id), so
provision is idempotent and the leak sweep matches instances to rows by name.

Agents are attached to one user-defined network and carry no published ports. On the
bundled stack that network is ``duckhaven_internal`` (``internal: true``), which is
what keeps an agent's result server reachable from the control plane and from nowhere
else — the single-host equivalent of the Azure delegated subnet and its NSG.
"""

from __future__ import annotations

import asyncio
import logging

from api.config import settings
from api.services.compute.backends import ProvisionRequest

logger = logging.getLogger(__name__)

_MANAGED_LABEL = "duckhaven-managed"


class DockerEngineBackend:
    provider = "docker"

    def _client(self):
        """Build a Docker client for the configured host (lazy SDK import)."""
        import docker

        host = settings.elastic_docker_host
        if not host:
            raise RuntimeError("docker backend requires elastic_docker_host")
        return docker.DockerClient(base_url=host)

    def _result_port(self) -> int:
        return 8001

    def _container_spec(self, req: ProvisionRequest) -> dict:
        """Keyword arguments for ``containers.create``.

        Split out so the spec can be asserted in tests without a daemon: a wrong
        payload here is the failure mode a fake client cannot otherwise catch.
        """
        polaris_url = settings.elastic_agent_polaris_base_url or settings.polaris_base_url
        memory_gb = req.memory_gb or settings.elastic_docker_memory_gb
        cpu = req.cpu or settings.elastic_docker_cpu

        return {
            "image": req.image,
            "name": req.instance_id,
            "detach": True,
            "environment": {
                "CONTROL_PLANE_URL": req.control_plane_url,
                "BOOTSTRAP_TOKEN": req.bootstrap_token,
                # The agent attaches workspace catalogs against Polaris directly.
                "POLARIS_BASE_URL": polaris_url,
                "POLARIS_CLIENT_ID": settings.polaris_client_id,
                "POLARIS_CLIENT_SECRET": settings.polaris_client_secret,
                # Bind all interfaces: the control plane reaches the result server
                # across the network by container address, not loopback.
                "RESULTS_HTTP_HOST": "0.0.0.0",
            },
            "labels": {**req.tags, _MANAGED_LABEL: "true"},
            # One user-defined network, no published ports. The result server is
            # reachable from the control plane and from nothing outside the host.
            "network": settings.elastic_docker_network,
            # Sandbox hardening, matching the static agent in deploy/docker-compose.yml
            # exactly. An elastic agent runs the same broad SQL surface governed by the
            # API's statement policy rather than a hard allowlist, so it has to be
            # contained at the OS layer the same way -- otherwise provisioning one
            # would be a quiet downgrade from running one by hand.
            "read_only": True,
            "tmpfs": {"/tmp": ""},
            "security_opt": ["no-new-privileges:true"],
            "cap_drop": ["ALL"],
            "pids_limit": 512,
            # A concrete memory.max for the agent's cgroup-aware sizing to read.
            "mem_limit": f"{int(memory_gb)}g",
            "nano_cpus": int(cpu * 1_000_000_000),
            # DuckHaven decides when an agent is torn down (via terminate), not Docker;
            # on a crash it should redial rather than stay dead.
            "restart_policy": {"Name": "on-failure"},
        }

    async def provision(self, req: ProvisionRequest) -> str:
        client = self._client()
        spec = self._container_spec(req)

        def _create() -> None:
            import docker.errors

            try:
                container = client.containers.create(**spec)
            except docker.errors.ImageNotFound:
                # A box that has never run a static agent has no local copy. Pull once
                # and retry rather than making `docker pull` an undocumented
                # prerequisite of the first elastic query.
                logger.info("Pulling agent image %s", spec["image"])
                client.images.pull(spec["image"])
                container = client.containers.create(**spec)
            container.start()

        await asyncio.to_thread(_create)
        return req.instance_id

    async def terminate(self, instance_id: str) -> None:
        client = self._client()

        def _remove() -> None:
            import docker.errors

            try:
                client.containers.get(instance_id).remove(force=True)
            except docker.errors.NotFound:
                # Already gone is the desired end state, not an error -- the reaper
                # may be racing a manual `docker rm`.
                pass

        await asyncio.to_thread(_remove)

    async def address(self, instance_id: str) -> str | None:
        """The container's address on the agent network, or ``None``.

        This is how the control plane learns where to fetch an agent's results. The
        address is assigned after creation, so it cannot be passed to the container as
        configuration.

        Returns ``None`` rather than raising, so a transient daemon failure leaves the
        caller free to fall back on the socket's peer address instead of failing the
        registration.
        """
        client = self._client()
        network = settings.elastic_docker_network

        def _get() -> str | None:
            container = client.containers.get(instance_id)
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            return (networks.get(network) or {}).get("IPAddress") or None

        try:
            return await asyncio.to_thread(_get)
        except Exception:
            logger.warning("Could not resolve address for instance %s", instance_id)
            return None

    async def status(self, instance_id: str) -> str:
        client = self._client()

        def _get() -> str:
            container = client.containers.get(instance_id)
            return container.attrs.get("State", {}).get("Status", "unknown")

        try:
            return await asyncio.to_thread(_get)
        except Exception:
            return "gone"

    async def list_managed(self) -> set[str]:
        client = self._client()

        def _list() -> set[str]:
            containers = client.containers.list(
                all=True, filters={"label": f"{_MANAGED_LABEL}=true"}
            )
            return {c.name for c in containers}

        return await asyncio.to_thread(_list)
