"""Azure Container Instances backend round-trip, env-gated.

There is no ACI emulator, so this runs only against a real Azure subscription. It
proves the management-SDK wiring end to end — create a container group, see it in
the leak-sweep listing, then tear it down — without depending on a publicly
reachable control plane (the full provision -> dial-home -> dispatch path is a
manual/staged verification, see the elastic-compute plan).

Enable by setting, in addition to Azure credentials in the ambient environment
(DefaultAzureCredential):

    RUN_AZURE_TESTS=1
    ELASTIC_AZURE_SUBSCRIPTION_ID=<sub>
    ELASTIC_AZURE_RESOURCE_GROUP=<rg>
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from api.config import settings
from api.services.compute.backends import ProvisionRequest

pytestmark = pytest.mark.asyncio


def _gated() -> bool:
    return bool(
        os.getenv("RUN_AZURE_TESTS")
        and os.getenv("ELASTIC_AZURE_SUBSCRIPTION_ID")
        and os.getenv("ELASTIC_AZURE_RESOURCE_GROUP")
    )


async def test_aci_provision_list_terminate_roundtrip(monkeypatch) -> None:
    if not _gated():
        pytest.skip("RUN_AZURE_TESTS + ELASTIC_AZURE_* not set; skipping ACI integration test")

    monkeypatch.setattr(
        settings, "elastic_azure_subscription_id", os.environ["ELASTIC_AZURE_SUBSCRIPTION_ID"]
    )
    monkeypatch.setattr(
        settings, "elastic_azure_resource_group", os.environ["ELASTIC_AZURE_RESOURCE_GROUP"]
    )
    monkeypatch.setattr(settings, "elastic_azure_cpu", 1.0)
    monkeypatch.setattr(settings, "elastic_azure_memory_gb", 1.0)

    from api.services.compute.azure_aci import AzureAciBackend

    backend = AzureAciBackend()
    instance_id = f"dh-agent-itest-{uuid.uuid4().hex[:8]}"
    req = ProvisionRequest(
        instance_id=instance_id,
        # A tiny public image; we exercise the backend lifecycle, not enrollment.
        image="mcr.microsoft.com/azuredocs/aci-helloworld",
        control_plane_url="wss://unused.invalid/agents/connect",
        bootstrap_token="unused",
        tags={"duckhaven-managed": "true", "duckhaven-test": "true"},
    )

    try:
        assert await backend.provision(req) == instance_id
        # The create is a long-running op; poll until the group is visible in the
        # managed listing (what the leak sweep reconciles against).
        for _ in range(30):
            if instance_id in await backend.list_managed():
                break
            await asyncio.sleep(2)
        else:
            pytest.fail("provisioned group never appeared in list_managed")
    finally:
        await backend.terminate(instance_id)

    # Deletion is also long-running; confirm it eventually leaves the listing.
    for _ in range(30):
        if instance_id not in await backend.list_managed():
            break
        await asyncio.sleep(2)
    else:
        pytest.fail("terminated group still present in list_managed")
