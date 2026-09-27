"""Agent runtimes in the deploy manifests.

The bundled agent, the API's default runtime and the image tags CI builds must all
name the same runtime, or a stack comes up with an agent the API routes nothing
to, or pulls a tag that was never published. These pin that agreement against the
manifest (duckhaven_shared.runtimes), which is where the default is changed.
"""

import re
from pathlib import Path

import yaml

from duckhaven_shared.runtimes import DEFAULT_RUNTIME_ID

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _load(name: str) -> dict:
    with (DEPLOY / name).open() as f:
        return yaml.safe_load(f)


def _default(expr: str) -> str:
    """The fallback value of a `${VAR:-default}` expression."""
    match = re.fullmatch(r"\$\{\w+:-(.*)\}", expr)
    assert match, expr
    return match.group(1)


def test_the_bundled_agent_runs_the_default_runtime_image():
    for name in ("docker-compose.yml", "docker-compose.ha.yml"):
        image = _load(name)["services"]["agent"]["image"]
        assert image.endswith("-duckdb${DEFAULT_RUNTIME:-" + DEFAULT_RUNTIME_ID + "}"), name


def test_the_api_defaults_to_the_same_runtime():
    api = _load("docker-compose.yml")["services"]["api"]["environment"]
    assert _default(api["DEFAULT_RUNTIME"]) == DEFAULT_RUNTIME_ID
    ha_api = _load("docker-compose.ha.yml")["x-api-base"]["environment"]
    assert _default(ha_api["DEFAULT_RUNTIME"]) == DEFAULT_RUNTIME_ID


def test_elastic_compute_resolves_images_by_runtime_unless_overridden():
    """A non-empty AGENT_IMAGE pins the default runtime's image, so it must default
    to empty or every deployment would run in override mode."""
    api = _load("docker-compose.elastic.yml")["services"]["api"]["environment"]
    assert api["AGENT_IMAGE"] == "${AGENT_IMAGE:-}"


def test_ci_e2e_builds_the_tag_the_bundled_agent_pulls():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert f"duckhaven-agent:ci-e2e-duckdb{DEFAULT_RUNTIME_ID}" in ci
