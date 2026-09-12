"""Bundled object-store wiring in the compose files.

Two things about the bundled store are easy to regress silently in the manifest
and expensive to discover at runtime:

1. The store must have a healthcheck and the services that need it must wait for
   it. It answers 503 on the S3 plane until storage, IAM and lock quorum are all
   up, so starting Polaris against a merely-running container races.
2. Buckets cannot be created by making a directory — the store writes through an
   erasure backend — so the bootstrap one-shot must exist and must complete
   before Polaris starts.

Scope note: this asserts the *manifest*, like test_compose_sandbox.py. That the
store actually serves those endpoints is covered by the integration suite.
"""

from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

with (DEPLOY / "docker-compose.yml").open() as f:
    DEV = yaml.safe_load(f)
with (DEPLOY / "docker-compose.ha.yml").open() as f:
    HA = yaml.safe_load(f)

COMPOSE_FILES = (("dev", DEV), ("ha", HA))


@pytest.mark.parametrize(("name", "compose"), COMPOSE_FILES)
def test_object_store_service_is_present_and_pinned(name, compose):
    """An unpinned tag on a pre-GA store is how a deployment breaks overnight."""
    svc = compose["services"]["objectstore"]
    image = svc["image"]
    assert "rustfs/rustfs" in image, name
    # The tag must resolve to a concrete default, never `latest`.
    default_tag = image.split(":-")[-1].rstrip("}")
    assert default_tag not in ("latest", "rc"), f"{name}: unpinned tag {default_tag!r}"


@pytest.mark.parametrize(("name", "compose"), COMPOSE_FILES)
def test_object_store_has_a_healthcheck(name, compose):
    """Its predecessor had none, which left Polaris racing an empty store."""
    health = compose["services"]["objectstore"].get("healthcheck")
    assert health is not None, name
    probe = " ".join(health["test"])
    assert "/health/ready" in probe, f"{name}: {probe}"


@pytest.mark.parametrize(("name", "compose"), COMPOSE_FILES)
def test_polaris_waits_for_the_bucket_to_exist(name, compose):
    """Polaris writes Iceberg metadata into the bucket the moment a catalog is
    created, so it must not start before the bootstrap has made one."""
    depends = compose["services"]["polaris"]["depends_on"]
    assert depends["objectstore-bootstrap"]["condition"] == "service_completed_successfully", name


@pytest.mark.parametrize(("name", "compose"), COMPOSE_FILES)
def test_bootstrap_waits_for_health_and_does_not_restart(name, compose):
    svc = compose["services"]["objectstore-bootstrap"]
    assert svc["depends_on"]["objectstore"]["condition"] == "service_healthy", name
    assert svc["restart"] == "no", name
    command = " ".join(svc["command"]) if isinstance(svc["command"], list) else svc["command"]
    # Idempotent: `rc ls` short-circuits the create, so `up` can be re-run.
    assert "rc ls" in command and "rc mb" in command, f"{name}: {command}"


@pytest.mark.parametrize(("name", "compose"), COMPOSE_FILES)
def test_object_store_is_reachable_from_the_agent_network(name, compose):
    """The agent joins duckhaven_internal alone, so the store has to be on it as
    well as on default — one network is not enough."""
    networks = compose["services"]["objectstore"]["networks"]
    for net in ("default", "duckhaven_internal"):
        assert net in networks, f"{name}:{net}"


@pytest.mark.parametrize(("name", "compose"), COMPOSE_FILES)
def test_new_buckets_are_held_at_strict_durability(name, compose):
    """RustFS defaults new buckets to `relaxed`, which drops the xl.meta fsync.
    A bundled store gets force-stopped constantly, so that default is wrong."""
    env = compose["services"]["objectstore"]["environment"]
    assert env["RUSTFS_NEW_BUCKET_DURABILITY_MODE"] == "strict", name


def test_the_object_store_is_named_for_what_it_is():
    """Naming the service after a vendor is what made the last swap a repo-wide
    sweep; the next one should be an image-tag change."""
    for name, compose in COMPOSE_FILES:
        assert "objectstore" in compose["services"], name
        assert "objectstore_data" in (compose.get("volumes") or {}), name
