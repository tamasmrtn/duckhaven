"""The Docker socket boundary in the elastic compose override.

Enabling elastic compute on a single host means giving the control plane a path to
the Docker daemon, which is the most dangerous privilege in the stack: creating a
container is close to root on the box. `docker-socket-proxy` narrows that path, and
these tests pin the narrowing.

The list of *denied* sections matters more than the allowed one. The proxy denies by
default, so a section is granted only by being named — which makes an accidental
`EXEC: 1` or `SECRETS: 1` a one-line change with no obvious blast radius at review
time. Asserting the denials means that change cannot land quietly.

Scope note, the same one as test_compose_sandbox.py: this asserts the *manifest*.
That the proxy actually refuses `/exec` at runtime is verified by the documented
commands in docs/deployment/homelab-elastic-setup.md. What CI guarantees is that the
wiring never silently regresses.
"""

from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

with (DEPLOY / "docker-compose.elastic.yml").open() as f:
    ELASTIC = yaml.safe_load(f)

PROXY = ELASTIC["services"]["docker-socket-proxy"]

# Exactly what the backend needs: create/inspect/remove/list agent containers, pull
# the agent image once, and resolve the network to attach them to.
EXPECTED_GRANTS = {
    "POST",
    "CONTAINERS",
    "ALLOW_START",
    "ALLOW_STOP",
    "ALLOW_RESTARTS",
    "IMAGES",
    "NETWORKS",
    # Read-only host CPU/memory, so the create-agent dialog can bound its sliders
    # to what this machine will actually run.
    "INFO",
}

# Sections that must never be granted. EXEC would let the control plane run commands
# in any container on the host; AUTH, SECRETS and CONFIGS expose credentials; SWARM,
# NODES, TASKS and SERVICES reach cluster state; VOLUMES and SYSTEM enumerate storage
# the deployment does not own. None is needed to start an agent.
#
# INFO is deliberately *not* here: reading the host's CPU and memory is what lets the
# create-agent dialog offer sizes this machine can run. It is a read, and a narrower
# one than CONTAINERS, which already lists every container on the box.
FORBIDDEN_GRANTS = {
    "EXEC",
    "AUTH",
    "SECRETS",
    "CONFIGS",
    "SWARM",
    "NODES",
    "TASKS",
    "SERVICES",
    "PLUGINS",
    "VOLUMES",
    "SYSTEM",
    "BUILD",
    "COMMIT",
    "DISTRIBUTION",
    "SESSION",
}


def test_proxy_grants_exactly_what_the_backend_needs():
    assert set(PROXY["environment"]) == EXPECTED_GRANTS


def test_proxy_grants_no_forbidden_section():
    """Belt and braces with the test above: if someone widens EXPECTED_GRANTS to
    make that one pass, this still fails for the sections that matter."""
    granted = PROXY["environment"]
    for section in FORBIDDEN_GRANTS:
        assert str(granted.get(section, "0")) == "0", section


def test_proxy_is_reachable_only_from_the_internal_network():
    """No published ports and no `default` network, so nothing on the host's
    interfaces — and nothing with plain outbound access — can reach the daemon
    through it."""
    assert PROXY["networks"] == ["duckhaven_internal"]
    assert "ports" not in PROXY


def test_proxy_container_is_hardened():
    assert PROXY["read_only"] is True
    assert "no-new-privileges:true" in PROXY["security_opt"]
    assert PROXY["cap_drop"] == ["ALL"]
    # haproxy binds its listener before dropping privileges; nothing else is added.
    assert PROXY.get("cap_add", []) == ["NET_BIND_SERVICE"]


def test_proxy_has_the_tmpfs_its_entrypoint_needs():
    """The entrypoint renders haproxy.cfg into /tmp on every start, so a read-only
    root without this crash-loops on "can't create /tmp/haproxy.cfg". Pinned
    because the failure only shows up at runtime — the manifest looks fine."""
    assert "/tmp" in PROXY["tmpfs"]
    assert "/run" in PROXY["tmpfs"]


def test_agent_network_name_is_pinned():
    """Compose prefixes network names with the project — `deploy_duckhaven_internal`
    — but an elastic agent is created through the Docker API, which knows nothing
    about compose projects. Without an explicit name the attach fails with "network
    duckhaven_internal not found", and only a real stack reveals it."""
    assert ELASTIC["networks"]["duckhaven_internal"]["name"] == "duckhaven_internal"
    api_env = ELASTIC["services"]["api"]["environment"]
    assert api_env["ELASTIC_DOCKER_NETWORK"] == "duckhaven_internal"


def test_proxy_mounts_the_socket_read_only():
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in PROXY["volumes"]


def test_api_points_at_the_proxy_and_not_the_socket():
    """The whole point of the override: the API reaches the daemon over TCP through
    the proxy, and never has the socket on its own filesystem."""
    api = ELASTIC["services"]["api"]
    assert api["environment"]["ELASTIC_DOCKER_HOST"] == "tcp://docker-socket-proxy:2375"
    assert api["environment"]["ELASTIC_PROVIDER"] == "docker"
    assert "volumes" not in api


def test_agents_are_provisioned_onto_the_isolated_network():
    """Same network the static agent sits on alone, so a provisioned agent is no
    more reachable than one started by hand."""
    api = ELASTIC["services"]["api"]
    assert api["environment"]["ELASTIC_DOCKER_NETWORK"] == "duckhaven_internal"


def test_no_pricing_is_configured_for_this_provider():
    """Nobody prices a container on a machine you already own.

    The docker backend reports no currency, so the UI renders no cost at all.
    Carrying zeroed Azure rates here would be dead config that reads as though it
    still drove something.
    """
    api = ELASTIC["services"]["api"]["environment"]
    assert not [k for k in api if "PRICE" in k or "CURRENCY" in k]


def test_static_agent_is_off_by_default_but_recoverable():
    """Scale-to-zero is the point, so no always-on agent — but via a profile, so an
    operator who wants one alongside can opt back in rather than edit the file."""
    assert ELASTIC["services"]["agent"]["profiles"] == ["static-agent"]
