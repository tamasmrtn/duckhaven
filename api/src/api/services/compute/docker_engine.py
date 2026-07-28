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

# The one path the agent must be able to write to, matching `results_dir` in
# agent/src/agent/config.py. It holds the session token the agent persists after
# authenticating, and the result Parquet the control plane fetches.
#
# Not configurable here on purpose: the backend does not set RESULTS_DIR, so the
# agent always uses its own default, and a second place to change it could only
# ever disagree with the first.
_AGENT_RESULTS_DIR = "/var/duckhaven-agent/results"


class DockerEngineBackend:
    provider = "docker"

    def __init__(self) -> None:
        # Filled on the first capacity() call; see the note there on caching.
        self._capacity: tuple[float, float] | None = None

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
        memory_gb = req.memory_gb or settings.elastic_default_memory_gb
        cpu = req.cpu or settings.elastic_default_cpu

        environment = {
            "CONTROL_PLANE_URL": req.control_plane_url,
            "BOOTSTRAP_TOKEN": req.bootstrap_token,
            # The agent attaches workspace catalogs against Polaris directly.
            "POLARIS_BASE_URL": polaris_url,
            "POLARIS_CLIENT_ID": settings.polaris_client_id,
            "POLARIS_CLIENT_SECRET": settings.polaris_client_secret,
            # Bind all interfaces: the control plane reaches the result server
            # across the network by container address, not loopback.
            "RESULTS_HTTP_HOST": "0.0.0.0",
        }
        # Agent-side tracing, which the static agent gets from compose. Without it
        # every span from a provisioned agent disappears and a dispatch trace ends
        # at the control plane's producer span -- and because the elastic overlay
        # takes the static agent out, switching to elastic loses agent tracing
        # entirely unless this is forwarded.
        if settings.otel_exporter_otlp_endpoint:
            environment["OTEL_EXPORTER_OTLP_ENDPOINT"] = settings.otel_exporter_otlp_endpoint
        # Anything else the operator tuned on their static agent. The agent's own
        # defaults match the compose defaults for every other variable, so this is
        # only needed by someone who changed one -- SANDBOX_DISABLED_FILESYSTEMS is
        # the realistic case. Applied last so it can override the above.
        environment.update(settings.elastic_agent_env)

        return {
            "image": req.image,
            "name": req.instance_id,
            "detach": True,
            "environment": environment,
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
            # An anonymous volume for the results directory. A read-only root is
            # only workable because the agent has exactly one writable path -- the
            # static agent gets it from the `agent_results` named volume, and
            # without the equivalent here the agent authenticates, fails to persist
            # its session token, and reconnects forever without ever registering.
            #
            # Anonymous rather than named: an elastic agent is disposable, so its
            # results should go when it does. The list form is what makes the daemon
            # create a volume; a dict would declare a bind mount to a host path.
            # Either way this rides on POST /containers/create, so the socket proxy
            # needs no access to the /volumes API.
            "volumes": [_AGENT_RESULTS_DIR],
            "security_opt": ["no-new-privileges:true"],
            "cap_drop": ["ALL"],
            "pids_limit": 512,
            # A concrete memory.max for the agent's cgroup-aware sizing to read.
            #
            # Bytes rather than a "{n}g" string: int() on the GiB value truncated
            # 1.5 GiB to 1, and anything below 1 GiB to "0g" -- which Docker reads as
            # *no limit at all*, handing the agent the whole host. nano_cpus below
            # has always been exact, so the two sliders disagreed for fractional
            # sizes. Bytes are exact for both and cannot round down to unlimited.
            "mem_limit": int(memory_gb * 1024**3),
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
                # v=True takes the anonymous results volume with the container.
                # Without it every provisioned agent leaves one behind, and nothing
                # else ever collects them.
                client.containers.get(instance_id).remove(force=True, v=True)
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

    async def capacity(self) -> tuple[float, float] | None:
        """The host's usable capacity, less what the control plane needs to keep.

        On a single box the API, Postgres, Polaris and MinIO run on the same machine
        an agent is provisioned onto, so the honest ceiling is not the whole host --
        offering it would let one query starve the stack serving it. The reserve is
        configurable because how much the rest of the stack needs depends on what
        else is deployed alongside.

        Cached: a machine does not change size while the process runs, and this is
        read whenever the create-agent dialog opens.

        ``None`` on failure rather than raising, so an unreachable daemon degrades
        to the conservative default instead of breaking the dialog.
        """
        if self._capacity is not None:
            return self._capacity

        def _info() -> tuple[float, float]:
            info = self._client().info()
            return float(info["NCPU"]), info["MemTotal"] / 1024**3

        try:
            ncpu, memory_gb = await asyncio.to_thread(_info)
        except Exception:
            logger.warning("Could not read Docker host capacity; using default limits")
            return None

        # Floored to whole units because the UI steps in whole units, and never
        # below one so a small host still offers a usable size.
        self._capacity = (
            max(1.0, float(int(ncpu - settings.elastic_docker_reserve_cpu))),
            max(1.0, float(int(memory_gb - settings.elastic_docker_reserve_memory_gb))),
        )
        return self._capacity

    async def list_managed(self) -> set[str]:
        client = self._client()

        def _list() -> set[str]:
            containers = client.containers.list(
                all=True, filters={"label": f"{_MANAGED_LABEL}=true"}
            )
            return {c.name for c in containers}

        return await asyncio.to_thread(_list)
