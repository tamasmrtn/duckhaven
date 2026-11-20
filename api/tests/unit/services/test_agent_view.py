"""The agent list is the same agents, field for field, as building each one alone."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from api.models.agent import Agent
from api.services.agent_view import build_agent_out, build_agent_outs, effective_status


def _agent(name: str, **kwargs) -> Agent:
    kwargs.setdefault("status", "unavailable")
    kwargs.setdefault("access_mode", "open")
    return Agent(id=uuid.uuid4(), name=name, created_at=datetime(2026, 1, 2, tzinfo=UTC), **kwargs)


def test_the_list_matches_building_each_agent_alone():
    caps = {
        "cores": 8,
        "extensions": ["iceberg", "httpfs"],
        "memory_limit_gb": 4.0,
        "duckdb_version": "1.5.6",
        "runtime_id": "duckdb-1.5",
    }
    agents = [
        _agent("connected", capabilities=caps, status="healthy"),
        _agent(
            "elastic",
            capabilities=caps,
            requested_cpu=2,
            requested_memory_gb=8,
            idle_timeout_s=900,
        ),
        _agent("no-capabilities", capabilities=None),
        _agent("empty-capabilities", capabilities={}),
        _agent("hidden", capabilities=caps),
    ]
    tiers = {a.id: "use" for a in agents if a.name != "hidden"}
    tiers[agents[1].id] = "admin"
    connected = {str(agents[0].id)}

    listed = build_agent_outs(agents, tiers, connected)

    one_by_one = [
        build_agent_out(a, status=effective_status(a, connected), access_tier=tiers[a.id])
        for a in agents
        if a.id in tiers
    ]
    assert [a.model_dump() for a in listed] == [a.model_dump() for a in one_by_one]
    names = ["connected", "elastic", "no-capabilities", "empty-capabilities"]
    assert [a.name for a in listed] == names
