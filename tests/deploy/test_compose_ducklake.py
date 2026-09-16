"""DuckLake's Postgres wiring in the compose files and the init script.

DuckLake has no credential vendor in front of its catalog: the DuckDB agent *is*
the catalog client, so it needs a Postgres login and a network path to Postgres.
That is a real widening of what a contained agent can reach, and the only thing
that makes it acceptable is the `ducklake_agent` role — CONNECT on `ducklake`
alone, with PUBLIC's default CONNECT on `duckhaven` revoked.

Scope note, matching test_compose_sandbox.py: this asserts the *manifest* and the
*script text*. Whether Postgres actually refuses the connection is proved by
api/tests/integration/test_ducklake_roles.py against a live server. What CI
guarantees here is that the wiring cannot silently regress — the revokes being
dropped, or agents being handed the `duckhaven` credential.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"

with (DEPLOY / "docker-compose.yml").open() as f:
    DEV = yaml.safe_load(f)
with (DEPLOY / "docker-compose.ha.yml").open() as f:
    HA = yaml.safe_load(f)

INIT_SQL = (DEPLOY / "postgres-init" / "20-create-ducklake-db.sh").read_text()
ENABLE_SQL = (ROOT / "scripts" / "enable-ducklake.sh").read_text()


def _networks(service: dict) -> list[str]:
    return list(service.get("networks") or [])


def test_postgres_joins_the_agent_network():
    """Without this the agent cannot reach the DuckLake catalog at all."""
    assert "duckhaven_internal" in _networks(DEV["services"]["postgres"])


def test_ha_reaches_postgres_through_the_proxy():
    """Agents must address the leader-follower proxy, not a Patroni node, so a
    failover moves DuckLake's catalog connection with everything else."""
    assert "duckhaven_internal" in _networks(HA["services"]["pg-haproxy"])
    for node in ("patroni-1", "patroni-2"):
        assert "duckhaven_internal" not in _networks(HA["services"][node]), node


def test_public_connect_is_revoked_on_the_control_plane_databases():
    """The load-bearing line. Postgres grants PUBLIC CONNECT on every database by
    default, so without these REVOKEs `ducklake_agent` reaches `duckhaven` —
    users, password hashes and session tokens."""
    for script in (INIT_SQL, ENABLE_SQL):
        assert "REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC" in script
        assert "REVOKE CONNECT ON DATABASE polaris FROM PUBLIC" in script


def test_agent_role_is_granted_only_the_ducklake_database():
    for script in (INIT_SQL, ENABLE_SQL):
        assert "GRANT CONNECT ON DATABASE ducklake TO ducklake_agent" in script
        for db in ("duckhaven", "polaris"):
            assert f"GRANT CONNECT ON DATABASE {db} TO ducklake_agent" not in script


def test_init_and_enable_scripts_are_idempotent():
    """Both run against deployments that may already have the role: the init
    script on a rebuilt data dir, the enable script on every invocation."""
    for script in (INIT_SQL, ENABLE_SQL):
        assert "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ducklake')" in script
        assert "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ducklake_agent')" in script


def test_api_gets_the_owner_credential_and_the_agent_role_separately():
    """Two different logins on purpose: the API does DDL and metadata reads as
    the owner, agents get the restricted role. Collapsing them into one would
    hand agents the control-plane database."""
    env = DEV["services"]["api"]["environment"]
    assert "duckhaven:" in env["DUCKLAKE_DATABASE_URL"]
    assert env["DUCKLAKE_DATABASE_URL"].endswith("/ducklake")
    assert "ducklake_agent" in env["DUCKLAKE_AGENT_USER"]
    assert "duckhaven" not in env["DUCKLAKE_AGENT_USER"]


def test_ducklake_is_off_by_default():
    """Iceberg + Polaris stays the default; enabling is an operator decision."""
    assert DEV["services"]["api"]["environment"]["DUCKLAKE_ENABLED"] == "${DUCKLAKE_ENABLED:-false}"


def test_postgres_service_can_set_the_agent_role_password():
    """The init script reads it from the postgres service's own environment."""
    assert "DUCKLAKE_AGENT_PASSWORD" in DEV["services"]["postgres"]["environment"]


def test_backups_cover_the_ducklake_catalog():
    """A DuckLake table's schema, snapshots and file list live only in that
    database — a backup without it leaves unreadable Parquet."""
    backup = (ROOT / "scripts" / "pg-backup.sh").read_text()
    assert "ducklake" in backup
    assert "pg_database WHERE datname = 'ducklake'" in backup
