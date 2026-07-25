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

    def _dns_fqdn(self, instance_id: str) -> str:
        """Public FQDN of the container group's result server.

        ACI assigns ``<dns_label>.<region>.azurecontainer.io`` to a public IP; the
        agent advertises this as its result host so the API (whose outbound differs
        from the agent's inbound) can fetch result Parquet."""
        return f"{instance_id}.{settings.elastic_azure_location}.azurecontainer.io"

    def _build_group(self, req: ProvisionRequest):
        from azure.mgmt.containerinstance.models import (
            Container,
            ContainerGroup,
            ContainerGroupIpAddressType,
            ContainerPort,
            EnvironmentVariable,
            IpAddress,
            OperatingSystemTypes,
            Port,
            ResourceRequests,
            ResourceRequirements,
        )

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
            # Bind all interfaces and tell the API to fetch results at the public
            # DNS label (its egress IP differs from this inbound address).
            EnvironmentVariable(name="RESULTS_HTTP_HOST", value="0.0.0.0"),
            EnvironmentVariable(
                name="RESULT_ADVERTISE_HOST", value=self._dns_fqdn(req.instance_id)
            ),
        ]

        container = Container(
            name="agent",
            image=req.image,
            resources=ResourceRequirements(
                requests=ResourceRequests(
                    cpu=req.cpu or settings.elastic_azure_cpu,
                    memory_in_gb=req.memory_gb or settings.elastic_azure_memory_gb,
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
            # Public IP + DNS label so the API can reach the result server.
            ip_address=IpAddress(
                type=ContainerGroupIpAddressType.PUBLIC,
                dns_name_label=req.instance_id,
                ports=[Port(port=result_port)],
            ),
            image_registry_credentials=self._registry_credentials(req.image),
            tags={**req.tags, _MANAGED_TAG: "true"},
        )

    def _registry_credentials(self, image: str):
        """Registry pull credentials for a private ACR image, if configured.

        ACI needs explicit credentials to pull from a private registry; public
        images (and unset creds) return None."""
        from azure.mgmt.containerinstance.models import ImageRegistryCredential

        server = settings.elastic_registry_server
        if not server or not settings.elastic_registry_username:
            return None
        return [
            ImageRegistryCredential(
                server=server,
                username=settings.elastic_registry_username,
                password=settings.elastic_registry_password,
            )
        ]

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

    async def status(self, instance_id: str) -> str:
        client, rg = self._client_and_rg()

        def _get() -> str:
            group = client.container_groups.get(rg, instance_id)
            return (group.provisioning_state or "unknown").lower()

        try:
            return await asyncio.to_thread(_get)
        except Exception:
            return "gone"

    async def list_managed(self) -> set[str]:
        client, rg = self._client_and_rg()

        def _list() -> set[str]:
            names = set()
            for group in client.container_groups.list_by_resource_group(rg):
                if (group.tags or {}).get(_MANAGED_TAG) == "true":
                    names.add(group.name)
            return names

        return await asyncio.to_thread(_list)
