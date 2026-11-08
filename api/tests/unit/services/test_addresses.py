"""Tests for the addresses the API trusts: connection peers and agent result URLs."""

from __future__ import annotations

import uuid

import pytest
from starlette.requests import HTTPConnection

from api.config import settings
from api.models.agent import Agent
from api.services.addresses import clean_host, clean_port, peer_address, result_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("agent-7", "agent-7"),
        ("agent_7.internal", "agent_7.internal"),
        ("10.0.0.5", "10.0.0.5"),
        ("[fd00::1]", "[fd00::1]"),
        ("  agent-7  ", "agent-7"),
    ],
)
def test_clean_host_keeps_a_bare_host(value, expected):
    assert clean_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "agent.example/path",
        "agent.example?x=1",
        "agent.example#x",
        "user@agent.example",
        "agent.example:9000",
        "http://agent.example",
        "agent example",
        "-agent",
        "",
        None,
        8001,
    ],
)
def test_clean_host_refuses_anything_else(value):
    assert clean_host(value) is None


@pytest.mark.parametrize(
    ("value", "expected"), [(8001, 8001), ("8001", 8001), (1, 1), (65535, 65535)]
)
def test_clean_port_keeps_a_port(value, expected):
    assert clean_port(value) == expected


@pytest.mark.parametrize("value", [0, 65536, -1, "x", None, True, [8001]])
def test_clean_port_refuses_anything_else(value):
    assert clean_port(value) is None


def _conn(forwarded_for: str | None, peer: str = "172.30.0.10") -> HTTPConnection:
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for is not None else []
    return HTTPConnection({"type": "http", "headers": headers, "client": (peer, 40000)})


def test_peer_address_ignores_forwarded_for_by_default():
    assert peer_address(_conn("10.0.0.5")) == "172.30.0.10"


@pytest.mark.parametrize(
    ("forwarded_for", "expected"),
    [
        ("10.0.0.5", "10.0.0.5"),
        # A proxy that appends keeps whatever the client sent on the left.
        ("10.1.1.1, 10.0.0.5", "10.0.0.5"),
        (None, "172.30.0.10"),
        ("   ", "172.30.0.10"),
        ("10.0.0.5/path", "172.30.0.10"),
    ],
)
def test_peer_address_takes_the_proxy_hop_behind_a_trusted_proxy(
    monkeypatch, forwarded_for, expected
):
    monkeypatch.setattr(settings, "trusted_proxy_ingress", True)
    assert peer_address(_conn(forwarded_for)) == expected


def test_peer_address_without_a_client_is_none():
    assert peer_address(HTTPConnection({"type": "http", "headers": [], "client": None})) is None


def test_result_url_names_the_result_file():
    query_id = uuid.uuid4()
    agent = Agent(name="a", status="healthy", result_host="10.0.0.5", result_port=8001)
    assert result_url(agent, query_id) == f"http://10.0.0.5:8001/results/{query_id}.parquet"


@pytest.mark.parametrize(
    ("host", "port"),
    [(None, 8001), ("10.0.0.5", None), ("agent.example/path", 8001), ("agent.example?x=1", 8001)],
)
def test_result_url_is_none_without_a_usable_address(host, port):
    agent = Agent(name="a", status="healthy", result_host=host, result_port=port)
    assert result_url(agent, uuid.uuid4()) is None
