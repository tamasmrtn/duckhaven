"""Docker Engine backend round-trip, env-gated.

The sibling of test_elastic_aci.py, and it exists for the same reason: the unit
tests assert the container *spec* against a fake client, which cannot tell whether
the daemon accepts it. A key the SDK renames, a hardening option the daemon rejects,
a network that does not exist — all of those pass a fake and fail a real create.

This one also asserts the hardening reached the container, because that is the claim
the backend makes and the reason an elastic agent is safe to run: read-only root, no
new privileges, no capabilities. A fake client can only prove we asked.

Unlike ACI there is no cloud to pay for — any machine with a Docker daemon can run
it. Enable with:

    RUN_DOCKER_TESTS=1
    ELASTIC_DOCKER_HOST=unix://var/run/docker.sock   (or tcp://... for a proxy)

The network is created and removed by the test, so it does not assume the compose
stack is up.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from api.config import settings
from api.services.compute.backends import ProvisionRequest

pytestmark = pytest.mark.asyncio

# The Kubernetes pause image: about 700 KB, and its whole job is to start and then
# sleep. That matters here — a container that exits immediately releases its IP, so
# an image with a short-lived command makes `address` look broken when it is not.
# It also needs no capabilities and no writable filesystem, so it survives the same
# hardening a real agent runs under.
#
# The backend lifecycle is what is under test, not agent enrollment; that is the
# cross-component layer's job.
_TEST_IMAGE = "registry.k8s.io/pause:3.9"


def _gated() -> bool:
    return bool(os.getenv("RUN_DOCKER_TESTS") and os.getenv("ELASTIC_DOCKER_HOST"))


@pytest.fixture
def docker_network(monkeypatch):
    """A throwaway network, so the test does not require the compose stack.

    Gated here as well as in the tests: a fixture runs before the test body, so
    without this an ungated run raises KeyError instead of skipping.
    """
    if not _gated():
        pytest.skip("RUN_DOCKER_TESTS + ELASTIC_DOCKER_HOST not set; skipping Docker test")

    import docker

    client = docker.DockerClient(base_url=os.environ["ELASTIC_DOCKER_HOST"])
    name = f"dh-itest-{uuid.uuid4().hex[:8]}"
    network = client.networks.create(name, driver="bridge", internal=True)

    monkeypatch.setattr(settings, "elastic_docker_host", os.environ["ELASTIC_DOCKER_HOST"])
    monkeypatch.setattr(settings, "elastic_docker_network", name)
    monkeypatch.setattr(settings, "elastic_docker_cpu", 1.0)
    monkeypatch.setattr(settings, "elastic_docker_memory_gb", 1.0)
    try:
        yield name
    finally:
        network.remove()


async def test_docker_provision_list_terminate_roundtrip(docker_network) -> None:
    if not _gated():
        pytest.skip("RUN_DOCKER_TESTS + ELASTIC_DOCKER_HOST not set; skipping Docker test")

    from api.services.compute.docker_engine import DockerEngineBackend

    backend = DockerEngineBackend()
    instance_id = f"dh-agent-itest-{uuid.uuid4().hex[:8]}"
    req = ProvisionRequest(
        instance_id=instance_id,
        image=_TEST_IMAGE,
        control_plane_url="ws://unused.invalid/agents/connect",
        bootstrap_token="unused",
        tags={"duckhaven-test": "true"},
    )

    try:
        assert await backend.provision(req) == instance_id

        # What the leak sweep reconciles against.
        assert instance_id in await backend.list_managed()

        # The address the control plane fetches results from. Assigned after
        # creation, so it may lag the create by a moment.
        for _ in range(15):
            address = await backend.address(instance_id)
            if address:
                break
            await asyncio.sleep(1)
        else:
            pytest.fail("provisioned container never reported an address")

        # The hardening the backend claims to apply, read back off the daemon. This
        # is the assertion a fake client cannot make: it can only show we asked.
        import docker

        client = docker.DockerClient(base_url=os.environ["ELASTIC_DOCKER_HOST"])
        attrs = client.containers.get(instance_id).attrs
        host_config = attrs["HostConfig"]

        assert host_config["ReadonlyRootfs"] is True
        assert host_config["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in host_config["SecurityOpt"]
        assert host_config["PidsLimit"] == 512
        assert host_config["Memory"] == 1 * 1024**3
        assert attrs["Config"]["Labels"]["duckhaven-managed"] == "true"

        # Attached to the agent network and nothing else, so its result server is
        # reachable from the control plane and from nowhere off the host.
        assert set(attrs["NetworkSettings"]["Networks"]) == {docker_network}
    finally:
        await backend.terminate(instance_id)

    assert instance_id not in await backend.list_managed()
    assert await backend.status(instance_id) == "gone"


async def test_terminate_is_idempotent(docker_network) -> None:
    """The reaper can race a manual `docker rm`, so a missing container is the
    desired end state rather than an error."""
    if not _gated():
        pytest.skip("RUN_DOCKER_TESTS + ELASTIC_DOCKER_HOST not set; skipping Docker test")

    from api.services.compute.docker_engine import DockerEngineBackend

    await DockerEngineBackend().terminate(f"dh-agent-never-existed-{uuid.uuid4().hex[:8]}")
