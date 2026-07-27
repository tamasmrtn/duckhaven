"""Azure Container Instances backend.

Provisions each elastic agent as a single-container **container group** running the
agent image with the two env vars it needs to enroll (``CONTROL_PLANE_URL`` +
``BOOTSTRAP_TOKEN``) — the exact same contract as the manual add-agent flow. The
agent then dials home and registers itself; this backend never reaches into the
container after creating it (I2 is untouched — creating a container is not dialing
the agent's control channel).

The Azure management SDK is synchronous, so every call is offloaded to a thread to
keep the event loop free. Imports are lazy so this module (and ``get_backend``)
loads without ``azure-mgmt-containerinstance`` installed — only real provisioning
needs it.

``instance_id`` is the container-group name (deterministic, from the agent id), so
provision is idempotent and the leak sweep matches instances to rows by name.

Groups are injected into a delegated subnet and carry a private address only, so an
agent's result server is reachable from the control plane's network and nowhere else.
The address is assigned after creation, which is why ``address`` exists.
"""

from __future__ import annotations

import asyncio
import logging

from api.config import settings
from api.services.compute.backends import ProvisionRequest

logger = logging.getLogger(__name__)

_MANAGED_TAG = "duckhaven-managed"


class AzureAciBackend:
    provider = "azure_aci"

    def _client_and_rg(self):
        """Build a management client + resolve the resource group (lazy SDK import)."""
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.containerinstance import ContainerInstanceManagementClient

        subscription = settings.elastic_azure_subscription_id
        rg = settings.elastic_azure_resource_group
        if not subscription or not rg:
            raise RuntimeError(
                "azure_aci backend requires elastic_azure_subscription_id and "
                "elastic_azure_resource_group"
            )
        client = ContainerInstanceManagementClient(DefaultAzureCredential(), subscription)
        return client, rg

    def _result_port(self) -> int:
        return 8001

    def _build_group(self, req: ProvisionRequest):
        from azure.mgmt.containerinstance.models import (
            Container,
            ContainerGroup,
            ContainerGroupIpAddressType,
            ContainerGroupSubnetId,
            ContainerPort,
            EnvironmentVariable,
            IpAddress,
            OperatingSystemTypes,
            Port,
            ResourceRequests,
            ResourceRequirements,
        )

        subnet_id = settings.elastic_azure_subnet_id
        if not subnet_id:
            raise RuntimeError("azure_aci backend requires elastic_azure_subnet_id")

        result_port = self._result_port()
        polaris_url = settings.elastic_agent_polaris_base_url or settings.polaris_base_url
        env = [
            EnvironmentVariable(name="CONTROL_PLANE_URL", value=req.control_plane_url),
            # Secrets go in secure_value so they are never returned by a GET.
            EnvironmentVariable(name="BOOTSTRAP_TOKEN", secure_value=req.bootstrap_token),
            # The agent attaches workspace catalogs against Polaris directly.
            EnvironmentVariable(name="POLARIS_BASE_URL", value=polaris_url),
            EnvironmentVariable(name="POLARIS_CLIENT_ID", value=settings.polaris_client_id),
            EnvironmentVariable(
                name="POLARIS_CLIENT_SECRET", secure_value=settings.polaris_client_secret
            ),
            # Bind all interfaces. The agent is not told its own address: a
            # subnet-injected group is assigned its private IP *after* creation, and
            # environment variables are fixed at creation, so there is nothing to put
            # in RESULT_ADVERTISE_HOST. The control plane resolves the address from
            # ARM instead — see ``address`` below.
            EnvironmentVariable(name="RESULTS_HTTP_HOST", value="0.0.0.0"),
        ]

        container = Container(
            name="agent",
            image=req.image,
            resources=ResourceRequirements(
                requests=ResourceRequests(
                    cpu=req.cpu or settings.elastic_default_cpu,
                    memory_in_gb=req.memory_gb or settings.elastic_default_memory_gb,
                )
            ),
            environment_variables=env,
            ports=[ContainerPort(port=result_port)],
        )
        return ContainerGroup(
            location=settings.elastic_azure_location,
            containers=[container],
            os_type=OperatingSystemTypes.LINUX,
            # The agent manages its own lifecycle; on a crash it should redial, but
            # DuckHaven decides when it is torn down (via delete), not Azure.
            restart_policy="OnFailure",
            # Injected into a delegated subnet with a private address and no DNS label.
            # ACI allows either a public IP with a label or subnet injection, never
            # both, so this is what keeps an agent's result server off the internet:
            # the only route to it is from inside the virtual network.
            subnet_ids=[ContainerGroupSubnetId(id=subnet_id)],
            ip_address=IpAddress(
                type=ContainerGroupIpAddressType.PRIVATE,
                ports=[Port(port=result_port)],
            ),
            image_registry_credentials=self._registry_credentials(req.image),
            identity=self._identity(),
            tags={**req.tags, _MANAGED_TAG: "true"},
        )

    def _registry_credentials(self, image: str):
        """Registry pull credentials for a private ACR image, if configured.

        ACI needs explicit credentials to pull from a private registry; public
        images (and an unset identity) return None.

        The credential is a user-assigned managed identity holding AcrPull, not a
        username and password. There is then no registry secret to store, rotate,
        or leak, and none appears in the container group spec -- which is readable
        by anyone with reader access to the resource group. Note that ACI supports
        *only* user-assigned identities for image pull, not system-assigned.
        """
        from azure.mgmt.containerinstance.models import ImageRegistryCredential

        server = settings.elastic_registry_server
        identity = settings.elastic_registry_identity_id
        if not server or not identity:
            return None
        return [ImageRegistryCredential(server=server, identity=identity)]

    def _identity(self):
        """The container group's own identity, which is what pulls the image."""
        from azure.mgmt.containerinstance.models import (
            ContainerGroupIdentity,
            ResourceIdentityType,
        )

        identity = settings.elastic_registry_identity_id
        if not identity:
            return None
        return ContainerGroupIdentity(
            type=ResourceIdentityType.USER_ASSIGNED,
            user_assigned_identities={identity: {}},
        )

    async def provision(self, req: ProvisionRequest) -> str:
        client, rg = self._client_and_rg()
        group = self._build_group(req)

        def _create() -> None:
            # begin_* issues the create and returns a poller immediately; we do not
            # block on .result() — the container provisions asynchronously and the
            # agent dials home when ready (that is the cold start we don't hold).
            client.container_groups.begin_create_or_update(rg, req.instance_id, group)

        await asyncio.to_thread(_create)
        return req.instance_id

    async def terminate(self, instance_id: str) -> None:
        client, rg = self._client_and_rg()

        def _delete() -> None:
            client.container_groups.begin_delete(rg, instance_id)

        await asyncio.to_thread(_delete)

    async def address(self, instance_id: str) -> str | None:
        """The instance's private address, or ``None`` if it has none yet.

        This is how the control plane learns where to fetch an agent's results.
        Nothing else can tell it: a subnet-injected group has no DNS name, the address
        is assigned after creation so it cannot be passed to the container as
        configuration, and the socket the agent dials home on arrives through a NAT
        gateway, so its peer address is the gateway's rather than the agent's.

        Returns ``None`` rather than raising, so a transient ARM failure leaves the
        caller free to fall back instead of failing the registration.
        """
        client, rg = self._client_and_rg()

        def _get() -> str | None:
            group = client.container_groups.get(rg, instance_id)
            return getattr(group.ip_address, "ip", None)

        try:
            return await asyncio.to_thread(_get)
        except Exception:
            logger.warning("Could not resolve address for instance %s", instance_id)
            return None

    async def status(self, instance_id: str) -> str:
        client, rg = self._client_and_rg()

        def _get() -> str:
            group = client.container_groups.get(rg, instance_id)
            return (group.provisioning_state or "unknown").lower()

        try:
            return await asyncio.to_thread(_get)
        except Exception:
            return "gone"

    async def capacity(self) -> tuple[float, float]:
        """Azure Container Instances' per-container-group ceiling in most regions.

        A constant rather than a query: the limit is a published quota, not a
        property of anything this deployment owns, and the regions that differ
        differ downward for reasons (GPU SKUs, restricted offers) that a capacity
        probe would not reveal either. ARM rejects an over-sized group at create,
        so this bound is advisory and the platform enforces the real one.
        """
        return 4.0, 16.0

    async def list_managed(self) -> set[str]:
        client, rg = self._client_and_rg()

        def _list() -> set[str]:
            names = set()
            for group in client.container_groups.list_by_resource_group(rg):
                if (group.tags or {}).get(_MANAGED_TAG) == "true":
                    names.add(group.name)
            return names

        return await asyncio.to_thread(_list)
