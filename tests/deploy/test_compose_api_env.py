"""Every setting in deploy/.env reaches the API container.

The API reads dozens of settings (LDAP, session lifetime, result cache, SQL
session limits, …) that the compose file does not list one by one. Listing them
is what used to happen, and the list fell behind: LDAP_ENABLED=true in .env,
exactly as the LDAP guide says, never reached the API, so LDAP silently stayed
off. The api services therefore load .env whole; explicit `environment:` entries
still take precedence for the values compose wires itself.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

with (DEPLOY / "docker-compose.yml").open() as f:
    DEV = yaml.safe_load(f)
with (DEPLOY / "docker-compose.ha.yml").open() as f:
    HA = yaml.safe_load(f)

OPTIONAL_DOTENV = {"path": ".env", "required": False}


def test_the_api_loads_dotenv_in_both_compose_files():
    assert OPTIONAL_DOTENV in DEV["services"]["api"]["env_file"]
    for replica in ("api-1", "api-2"):
        assert OPTIONAL_DOTENV in HA["services"][replica]["env_file"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker compose CLI")
def test_a_setting_compose_does_not_list_reaches_the_api(tmp_path):
    shutil.copy(DEPLOY / "docker-compose.yml", tmp_path / "docker-compose.yml")
    (tmp_path / ".env").write_text("LDAP_ENABLED=true\nCOOKIE_SECURE=true\n")

    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(tmp_path / "docker-compose.yml"),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    env = json.loads(rendered.stdout)["services"]["api"]["environment"]

    assert env["LDAP_ENABLED"] == "true"
    # Values compose wires itself still come from its own `environment:` block,
    # which interpolates the same .env.
    assert env["COOKIE_SECURE"] == "true"
