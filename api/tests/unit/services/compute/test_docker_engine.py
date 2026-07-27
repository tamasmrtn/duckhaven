"""The container spec built for an elastic agent on a Docker host.

Pure construction — no daemon involved. Two properties are worth asserting here.

The first is isolation: an agent joins one user-defined network and publishes no
ports, which on the bundled stack (`duckhaven_internal`, `internal: true`) is what
keeps its result server reachable from the control plane and from nowhere else.

The second is that an agent *provisioned for you* is contained exactly as tightly as
one you start by hand. The static agent in deploy/docker-compose.yml runs read-only
with no-new-privileges, all capabilities dropped and a pids cap, because the API's
statement policy governs which SQL reaches DuckDB rather than a hard allowlist. If
the elastic path quietly dropped any of that it would be a downgrade disguised as a
feature, so each flag is asserted individually rather than as a blob.
"""

import docker.errors
import pytest

from api.config import settings
from api.services.compute.backends import ProvisionRequest
from api.services.compute.docker_engine import DockerEngineBackend

NETWORK = "duckhaven_internal"


@pytest.fixture
def req() -> ProvisionRequest:
    return ProvisionRequest(
        instance_id="dh-agent-0123456789abcdef0123",
        image="ghcr.io/tamasmrtn/duckhaven-agent:1.0.0",
        control_plane_url="ws://api:8000/agents/connect",
        bootstrap_token="dh_boot_secret",
        cpu=2.0,
        memory_gb=8.0,
        tags={"duckhaven-managed": "true"},
    )


@pytest.fixture(autouse=True)
def docker_settings(monkeypatch):
    monkeypatch.setattr(settings, "elastic_docker_network", NETWORK)
    monkeypatch.setattr(settings, "elastic_docker_host", "tcp://docker-socket-proxy:2375")


def test_agent_joins_one_network_and_publishes_no_ports(req):
    spec = DockerEngineBackend()._container_spec(req)

    assert spec["network"] == NETWORK
    # Publishing a port would put the result server on the host's interfaces, which
    # is the one thing the isolated network exists to prevent.
    assert "ports" not in spec


def test_agent_is_hardened_exactly_like_the_static_one(req):
    spec = DockerEngineBackend()._container_spec(req)

    assert spec["read_only"] is True
    assert spec["tmpfs"] == {"/tmp": ""}
    assert spec["security_opt"] == ["no-new-privileges:true"]
    assert spec["cap_drop"] == ["ALL"]
    assert spec["pids_limit"] == 512


def test_agent_gets_a_writable_results_volume(req):
    """The other half of the read-only root, and the half that is easy to forget.

    The agent persists its session token under the results directory immediately
    after authenticating. Without a writable mount there it authenticates, fails
    the write, disconnects and reconnects forever — registering successfully every
    time and completing registration never, which surfaces as an agent stuck in
    provisioning rather than as an obvious permissions error.

    The list form matters: a dict would declare a bind mount to a host path.
    """
    spec = DockerEngineBackend()._container_spec(req)

    assert spec["volumes"] == ["/var/duckhaven-agent/results"]


def test_requested_size_becomes_concrete_limits(req):
    """The agent reads its own cgroup to advertise capacity, so the memory cap has
    to be a real limit rather than left unbounded."""
    spec = DockerEngineBackend()._container_spec(req)

    assert spec["mem_limit"] == "8g"
    assert spec["nano_cpus"] == 2_000_000_000


def test_size_falls_back_to_configured_defaults(req, monkeypatch):
    monkeypatch.setattr(settings, "elastic_docker_cpu", 1.0)
    monkeypatch.setattr(settings, "elastic_docker_memory_gb", 2.0)
    req.cpu = None
    req.memory_gb = None

    spec = DockerEngineBackend()._container_spec(req)

    assert spec["mem_limit"] == "2g"
    assert spec["nano_cpus"] == 1_000_000_000


def test_enrollment_contract_is_unchanged(req):
    """An elastic agent is the same container as a static one; it just gets started
    for you. The two enrollment variables are the whole contract."""
    spec = DockerEngineBackend()._container_spec(req)
    env = spec["environment"]

    assert env["CONTROL_PLANE_URL"] == "ws://api:8000/agents/connect"
    assert env["BOOTSTRAP_TOKEN"] == "dh_boot_secret"
    # The control plane reaches the result server by container address, not loopback.
    assert env["RESULTS_HTTP_HOST"] == "0.0.0.0"
    assert "RESULT_ADVERTISE_HOST" not in env


def test_managed_label_is_always_stamped(req):
    """The leak sweep matches on this label; an unlabelled container would be
    invisible to it and leak silently."""
    req.tags = {}
    spec = DockerEngineBackend()._container_spec(req)

    assert spec["labels"]["duckhaven-managed"] == "true"


def test_container_is_named_by_instance_id(req):
    """Deterministic from the agent id, so a crash between create and record is
    reconcilable rather than a silent leak."""
    spec = DockerEngineBackend()._container_spec(req)

    assert spec["name"] == "dh-agent-0123456789abcdef0123"


# ── Client interaction ────────────────────────────────────────────────────────


class _FakeContainer:
    def __init__(self, name: str, attrs: dict | None = None) -> None:
        self.name = name
        self.attrs = attrs or {}
        self.started = False
        self.removed = False

    def start(self) -> None:
        self.started = True

    def remove(self, force: bool = False, v: bool = False) -> None:
        self.removed = True
        self.removed_volumes = v


class _FakeContainers:
    def __init__(self, existing: dict[str, _FakeContainer] | None = None) -> None:
        self.existing = existing or {}
        self.created: list[dict] = []
        self.create_raises: list[Exception] = []

    def create(self, **kwargs) -> _FakeContainer:
        if self.create_raises:
            raise self.create_raises.pop(0)
        self.created.append(kwargs)
        container = _FakeContainer(kwargs["name"])
        self.existing[kwargs["name"]] = container
        return container

    def get(self, name: str) -> _FakeContainer:
        if name not in self.existing:
            raise docker.errors.NotFound(name)
        return self.existing[name]

    def list(self, all: bool = False, filters: dict | None = None):  # noqa: A002
        self.list_args = {"all": all, "filters": filters}
        return list(self.existing.values())


class _FakeImages:
    def __init__(self) -> None:
        self.pulled: list[str] = []

    def pull(self, image: str) -> None:
        self.pulled.append(image)


class _FakeClient:
    def __init__(self, containers: _FakeContainers | None = None) -> None:
        self.containers = containers or _FakeContainers()
        self.images = _FakeImages()


@pytest.fixture
def backend(monkeypatch):
    """A backend whose _client() returns a fake, so no daemon is contacted."""
    client = _FakeClient()
    b = DockerEngineBackend()
    monkeypatch.setattr(b, "_client", lambda: client)
    b.fake = client  # type: ignore[attr-defined]
    return b


async def test_provision_creates_and_starts(backend, req):
    await backend.provision(req)

    assert len(backend.fake.containers.created) == 1
    assert backend.fake.containers.existing[req.instance_id].started is True


async def test_provision_pulls_a_missing_image_and_retries(backend, req):
    """A box that has never run a static agent has no local copy, so the first
    elastic query would otherwise fail on a missing image."""
    backend.fake.containers.create_raises = [docker.errors.ImageNotFound("nope")]

    await backend.provision(req)

    assert backend.fake.images.pulled == [req.image]
    assert len(backend.fake.containers.created) == 1


async def test_terminate_removes_the_container_and_its_volume(backend, req):
    """The anonymous results volume has to go with the container: nothing else
    ever collects them, so an uncleaned one leaks per provisioned agent."""
    await backend.provision(req)

    await backend.terminate(req.instance_id)

    container = backend.fake.containers.existing[req.instance_id]
    assert container.removed is True
    assert container.removed_volumes is True


async def test_terminate_tolerates_an_already_gone_container(backend):
    """Already gone is the desired end state: the reaper can race a manual rm."""
    await backend.terminate("dh-agent-vanished")


async def test_address_reads_the_agent_network_ip(backend, req):
    backend.fake.containers.existing[req.instance_id] = _FakeContainer(
        req.instance_id,
        {"NetworkSettings": {"Networks": {NETWORK: {"IPAddress": "172.20.0.7"}}}},
    )

    assert await backend.address(req.instance_id) == "172.20.0.7"


async def test_address_is_none_before_an_ip_is_assigned(backend, req):
    backend.fake.containers.existing[req.instance_id] = _FakeContainer(
        req.instance_id,
        {"NetworkSettings": {"Networks": {NETWORK: {"IPAddress": ""}}}},
    )

    assert await backend.address(req.instance_id) is None


async def test_address_is_none_rather_than_raising_when_the_daemon_fails(backend):
    """So a transient failure lets the caller fall back on the socket's peer address
    instead of failing the registration outright."""
    assert await backend.address("dh-agent-vanished") is None


async def test_status_reports_gone_for_a_missing_container(backend):
    assert await backend.status("dh-agent-vanished") == "gone"


async def test_status_reads_the_container_state(backend, req):
    backend.fake.containers.existing[req.instance_id] = _FakeContainer(
        req.instance_id, {"State": {"Status": "running"}}
    )

    assert await backend.status(req.instance_id) == "running"


async def test_list_managed_filters_on_the_label(backend, req):
    """The filter is what stops the leak sweep terminating containers this
    deployment does not own — every other container on a homelab box carries no
    such label, and the reaper terminates whatever this returns."""
    await backend.provision(req)

    assert await backend.list_managed() == {req.instance_id}
    # Filtering happens daemon-side, so an unlabelled container is never even
    # returned; asserting the arguments is the only way to see that from here.
    assert backend.fake.containers.list_args == {
        "all": True,
        "filters": {"label": "duckhaven-managed=true"},
    }
