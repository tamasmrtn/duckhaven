"""Runtimes from the control plane's side: images, trust, dispatch and ranking."""

from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from api.config import settings
from api.models.agent import Agent
from api.services import runtimes as svc
from duckhaven_shared import runtimes

_BASE = runtimes.RUNTIMES["1.5"]


@pytest.fixture
def extra_runtimes(monkeypatch):
    """A beta, a deprecated and a retired runtime next to the real 1.5."""
    for rid, status in (("2.0", "beta"), ("1.4", "deprecated"), ("1.3", "retired")):
        monkeypatch.setitem(
            runtimes.RUNTIMES,
            rid,
            replace(_BASE, id=rid, display_name=f"DuckDB {rid}", duckdb_line=rid, status=status),
        )


def _agent(caps: dict | None = None, *, requested: str | None = None, name: str = "a") -> Agent:
    return Agent(
        id=uuid.uuid4(),
        name=name,
        status="healthy",
        capabilities=caps,
        requested_runtime_id=requested,
    )


def _caps(runtime_id: str | None, engine: str, extensions=("httpfs", "iceberg"), **extra):
    caps = {
        "duckdb_version": f"{engine.lstrip('v')} (with duckdb {engine.lstrip('v')})",
        "engine_version": engine,
        "extensions": list(extensions),
        **extra,
    }
    if runtime_id is not None:
        caps["runtime_id"] = runtime_id
    return caps


def _catalog(kind="iceberg_polaris", backend="object_store", slug="c"):
    return SimpleNamespace(kind=kind, slug=slug, storage_backend=SimpleNamespace(kind=backend))


# ── image_for ─────────────────────────────────────────────────────────────────


def test_image_for_a_dev_build_uses_latest(monkeypatch):
    monkeypatch.setattr(settings, "app_version", "0.0.0-dev")
    monkeypatch.setattr(settings, "agent_image_tag", "")
    monkeypatch.setattr(settings, "agent_image", "")
    assert svc.image_for("1.5") == "ghcr.io/tamasmrtn/duckhaven-agent:latest-duckdb1.5"


def test_image_for_a_release_matches_the_api_version(monkeypatch):
    monkeypatch.setattr(settings, "app_version", "1.4.0")
    monkeypatch.setattr(settings, "agent_image_tag", "")
    monkeypatch.setattr(settings, "agent_image", "")
    monkeypatch.setattr(settings, "agent_image_repository", "acr.example/duckhaven-agent")
    assert svc.image_for("2.0") == "acr.example/duckhaven-agent:1.4.0-duckdb2.0"


def test_the_deprecated_override_applies_to_the_default_runtime_only(monkeypatch):
    monkeypatch.setattr(settings, "agent_image", "localhost/agent:bench")
    monkeypatch.setattr(settings, "agent_image_tag", "ci")
    assert svc.image_for(settings.default_runtime) == "localhost/agent:bench"
    assert svc.image_for("2.0").endswith(":ci-duckdb2.0")


# ── resolve ───────────────────────────────────────────────────────────────────


def test_an_agent_that_has_not_reported_is_pending():
    resolved = svc.resolve(_agent(None, requested="1.5"))
    assert resolved.state == "pending"
    assert resolved.runtime is _BASE


def test_a_curated_runtime_on_its_line_is_ok():
    assert svc.resolve(_agent(_caps("1.5", "v1.5.5"))).state == "ok"


def test_a_pre_runtime_image_is_inferred_from_its_line():
    """Agents built before runtimes report none; their DuckDB line vouches for them."""
    resolved = svc.resolve(_agent({"duckdb_version": "1.5.4", "extensions": ["httpfs"]}))
    assert resolved.state == "inferred"
    assert resolved.runtime is _BASE


def test_an_unknown_runtime_is_unrecognized():
    assert svc.resolve(_agent(_caps("9.9", "v9.9.0"))).state == "unrecognized"


def test_a_runtime_on_the_wrong_duckdb_line_is_unrecognized():
    """A claim the engine contradicts is not trusted."""
    assert svc.resolve(_agent(_caps("1.5", "v2.0.0"))).state == "unrecognized"


def test_an_elastic_agent_running_something_else_is_a_mismatch(extra_runtimes):
    assert svc.resolve(_agent(_caps("2.0", "v2.0.1"), requested="1.5")).state == "mismatch"


def test_a_retired_runtime_is_retired(extra_runtimes):
    assert svc.resolve(_agent(_caps("1.3", "v1.3.2"))).state == "retired"


# ── assert_dispatchable ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("caps", "code"),
    [
        (_caps("9.9", "v9.9.0"), "runtime_unsupported"),
        (_caps("1.3", "v1.3.2"), "runtime_retired"),
        (_caps("1.5", "v1.5.5", extensions=("iceberg",)), "agent_incompatible"),
    ],
)
def test_dispatch_is_refused_with_the_reason(extra_runtimes, caps, code):
    with pytest.raises(svc.AgentNotDispatchable) as exc:
        svc.assert_dispatchable(_agent(caps), [_catalog()])
    assert exc.value.code == code


def test_a_beta_runtime_takes_work_that_names_it(extra_runtimes):
    svc.assert_dispatchable(_agent(_caps("2.0", "v2.0.1")), [_catalog()])


def test_a_session_needs_a_lock_that_applies():
    agent = _agent(_caps("1.5", "v1.5.5", sandbox="failed"))
    svc.assert_dispatchable(agent, [_catalog()])  # a one-shot query still runs
    with pytest.raises(svc.AgentNotDispatchable) as exc:
        svc.assert_dispatchable(agent, [_catalog()], for_session=True)
    assert exc.value.code == "agent_sandbox_unverified"


@pytest.mark.parametrize("sandbox", ["verified", "disabled", None])
def test_a_session_is_allowed_when_the_lock_is_verified_disabled_or_unreported(sandbox):
    """`disabled` is an operator's decision and `None` an older agent: neither is a
    lock that silently failed."""
    caps = _caps("1.5", "v1.5.5")
    if sandbox is not None:
        caps["sandbox"] = sandbox
    svc.assert_dispatchable(_agent(caps), [_catalog()], for_session=True)


# ── auto_pick_rank ────────────────────────────────────────────────────────────


def test_auto_pick_prefers_the_default_then_ga_then_deprecated(extra_runtimes, monkeypatch):
    monkeypatch.setitem(
        runtimes.RUNTIMES, "1.6", replace(_BASE, id="1.6", duckdb_line="1.6", status="ga")
    )
    assert svc.auto_pick_rank(_agent(_caps("1.5", "v1.5.5"))) == 0
    assert svc.auto_pick_rank(_agent(_caps("1.6", "v1.6.0"))) == 1
    assert svc.auto_pick_rank(_agent(_caps("1.4", "v1.4.3"))) == 2


@pytest.mark.parametrize(
    "caps",
    [_caps("2.0", "v2.0.1"), _caps("9.9", "v9.9.0"), _caps("1.3", "v1.3.2"), None],
    ids=["beta", "unrecognized", "retired", "pending"],
)
def test_auto_pick_never_chooses_these(extra_runtimes, caps):
    assert svc.auto_pick_rank(_agent(caps)) is None


# ── assert_restartable ────────────────────────────────────────────────────────


def test_a_retired_runtime_cannot_restart(extra_runtimes):
    with pytest.raises(svc.RuntimeRetired):
        svc.assert_restartable(_agent(requested="1.3"))
    assert svc.assert_restartable(_agent(requested="1.5")) is _BASE


def test_an_elastic_agent_from_before_runtimes_restarts_on_the_default():
    assert svc.assert_restartable(_agent(requested=None)).id == settings.default_runtime


# ── config ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["9.9", "2.0"])
def test_the_default_runtime_must_be_curated_and_not_beta(extra_runtimes, value):
    from pydantic import ValidationError

    from api.config import Settings

    with pytest.raises(ValidationError):
        Settings(default_runtime=value)
