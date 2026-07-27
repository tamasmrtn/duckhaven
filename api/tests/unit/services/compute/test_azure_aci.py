"""The container group spec built for an elastic agent.

Pure construction — no Azure involved. What is worth asserting here is the property the
deployment's network isolation rests on: an agent runs on a private address inside a
delegated subnet, with no public name, so its result server is reachable from the
control plane's network and nowhere else.
"""

import pytest

from api.config import settings
from api.services.compute.azure_aci import AzureAciBackend
from api.services.compute.backends import ProvisionRequest

SUBNET_ID = (
    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network"
    "/virtualNetworks/vnet/subnets/snet-aci"
)


@pytest.fixture
def req() -> ProvisionRequest:
    return ProvisionRequest(
        instance_id="dh-agent-0123456789abcdef0123",
        image="example.azurecr.io/duckhaven-agent:1.0.0",
        control_plane_url="wss://api.example.com/agents/connect",
        bootstrap_token="dh_boot_secret",
        cpu=2.0,
        memory_gb=8.0,
        tags={"duckhaven-managed": "true"},
    )


@pytest.fixture
def with_subnet(monkeypatch):
    monkeypatch.setattr(settings, "elastic_azure_subnet_id", SUBNET_ID)


def test_group_is_subnet_injected_with_a_private_address(with_subnet, req):
    group = AzureAciBackend()._build_group(req)

    assert [s.id for s in group.subnet_ids] == [SUBNET_ID]
    assert group.ip_address.type == "Private"
    assert [p.port for p in group.ip_address.ports] == [8001]


def test_group_has_no_public_dns_label(with_subnet, req):
    """A DNS label is only valid alongside a public address, and Container Instances
    permits either a public address with a label or subnet injection -- never both. Its
    absence is what keeps the result server off the internet."""
    group = AzureAciBackend()._build_group(req)

    assert group.ip_address.dns_name_label is None


def test_agent_is_not_told_its_own_address(with_subnet, req):
    """A private address is assigned after creation, while environment variables are
    fixed at creation, so there is nothing to put in RESULT_ADVERTISE_HOST. The control
    plane resolves the address from ARM instead."""
    group = AzureAciBackend()._build_group(req)
    names = {e.name for e in group.containers[0].environment_variables}

    assert "RESULT_ADVERTISE_HOST" not in names
    # The enrollment contract is otherwise unchanged.
    assert {"CONTROL_PLANE_URL", "BOOTSTRAP_TOKEN", "RESULTS_HTTP_HOST"} <= names


def test_bootstrap_token_is_not_readable_back(with_subnet, req):
    """Secrets go in secure_value, which ARM never returns on a GET."""
    group = AzureAciBackend()._build_group(req)
    token = next(
        e for e in group.containers[0].environment_variables if e.name == "BOOTSTRAP_TOKEN"
    )

    assert token.value is None
    assert token.secure_value == "dh_boot_secret"


def test_missing_subnet_fails_loudly(monkeypatch, req):
    """Rather than silently falling back to a public address, which would defeat the
    isolation and expose every agent."""
    monkeypatch.setattr(settings, "elastic_azure_subnet_id", None)

    with pytest.raises(RuntimeError, match="elastic_azure_subnet_id"):
        AzureAciBackend()._build_group(req)


AGENT_IDENTITY_ID = (
    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ManagedIdentity"
    "/userAssignedIdentities/id-duckhaven-agent-prod"
)


@pytest.fixture
def with_registry_identity(monkeypatch):
    monkeypatch.setattr(settings, "elastic_registry_server", "example.azurecr.io")
    monkeypatch.setattr(settings, "elastic_registry_identity_id", AGENT_IDENTITY_ID)


def test_image_is_pulled_with_an_identity_not_a_password(with_subnet, with_registry_identity, req):
    """A registry password would sit in plain text in the group spec, readable by
    anyone with reader access to the agents resource group."""
    group = AzureAciBackend()._build_group(req)
    credential = group.image_registry_credentials[0]

    assert credential.server == "example.azurecr.io"
    assert credential.identity == AGENT_IDENTITY_ID
    assert credential.username is None
    assert credential.password is None


def test_group_carries_the_pull_identity(with_subnet, with_registry_identity, req):
    """The credential above only works if the group is actually assigned the
    identity; ACI supports user-assigned only, never system-assigned, for this."""
    group = AzureAciBackend()._build_group(req)

    assert group.identity.type == "UserAssigned"
    assert AGENT_IDENTITY_ID in group.identity.user_assigned_identities


def test_public_image_needs_no_credentials(with_subnet, monkeypatch, req):
    monkeypatch.setattr(settings, "elastic_registry_server", None)
    monkeypatch.setattr(settings, "elastic_registry_identity_id", None)

    group = AzureAciBackend()._build_group(req)

    assert group.image_registry_credentials is None
    assert group.identity is None
