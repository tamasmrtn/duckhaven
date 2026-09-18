"""DuckLake's Postgres wiring in the compose files and the init script.

Asserts the manifest and script text, as test_compose_sandbox.py does; that
Postgres actually refuses the connection is proved by
api/tests/integration/test_ducklake_roles.py against a live server.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"

with (DEPLOY / "docker-compose.yml").open() as f:
    DEV = yaml.safe_load(f)
with (DEPLOY / "docker-compose.ha.yml").open() as f:
    HA = yaml.safe_load(f)

AGENT_DOCKERFILE = (ROOT / "agent" / "Dockerfile").read_text()
CHANNEL = (ROOT / "agent" / "src" / "agent" / "control" / "channel.py").read_text()

INIT_SQL = (DEPLOY / "postgres-init" / "20-create-ducklake-db.sh").read_text()
ENABLE_SQL = (ROOT / "scripts" / "enable-ducklake.sh").read_text()


def _networks(service: dict) -> list[str]:
    return list(service.get("networks") or [])


def test_postgres_joins_the_agent_network():
    """Without this the agent cannot reach the catalog."""
    assert "duckhaven_internal" in _networks(DEV["services"]["postgres"])


def test_ha_reaches_postgres_through_the_proxy():
    """Agents address the proxy, not a Patroni node, so failover moves the
    catalog connection with everything else."""
    assert "duckhaven_internal" in _networks(HA["services"]["pg-haproxy"])
    for node in ("patroni-1", "patroni-2"):
        assert "duckhaven_internal" not in _networks(HA["services"][node]), node


def test_public_connect_is_revoked_on_the_control_plane_databases():
    """Postgres grants PUBLIC CONNECT by default, so without these REVOKEs
    `ducklake_agent` reaches `duckhaven`: users, hashes, session tokens."""
    for script in (INIT_SQL, ENABLE_SQL):
        assert "REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC" in script
        assert "REVOKE CONNECT ON DATABASE polaris FROM PUBLIC" in script


def test_agent_role_is_granted_only_the_ducklake_database():
    for script in (INIT_SQL, ENABLE_SQL):
        assert "GRANT CONNECT ON DATABASE ducklake TO ducklake_agent" in script
        for db in ("duckhaven", "polaris"):
            assert f"GRANT CONNECT ON DATABASE {db} TO ducklake_agent" not in script


def test_init_and_enable_scripts_are_idempotent():
    """The init script can run on a rebuilt data dir, the enable script on every
    invocation."""
    for script in (INIT_SQL, ENABLE_SQL):
        assert "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ducklake')" in script
        assert "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ducklake_agent')" in script


def test_api_gets_the_owner_credential_and_the_agent_role_separately():
    """The API is the owner; agents get the restricted role. Collapsing them
    would hand agents the control-plane database."""
    env = DEV["services"]["api"]["environment"]
    # Default points the API at the ducklake database as owner.
    url = env["DUCKLAKE_DATABASE_URL"]
    assert "duckhaven:" in url
    assert url.rstrip("}").endswith("/ducklake")
    assert "ducklake_agent" in env["DUCKLAKE_AGENT_USER"]
    assert "duckhaven" not in env["DUCKLAKE_AGENT_USER"]


def test_ducklake_is_off_by_default():
    """Iceberg + Polaris stays the default; enabling is an operator decision."""
    assert DEV["services"]["api"]["environment"]["DUCKLAKE_ENABLED"] == "${DUCKLAKE_ENABLED:-false}"


def test_postgres_service_can_set_the_agent_role_password():
    """Read from the postgres service's own environment."""
    assert "DUCKLAKE_AGENT_PASSWORD" in DEV["services"]["postgres"]["environment"]


def test_backups_cover_the_ducklake_catalog():
    """Schema, snapshots and file list live only in that database; a backup
    without it leaves unreadable Parquet."""
    backup = (ROOT / "scripts" / "pg-backup.sh").read_text()
    assert "ducklake" in backup
    assert "pg_database WHERE datname = 'ducklake'" in backup


def test_agent_image_bakes_the_ducklake_extensions():
    """The agent is on an `internal: true` network and cannot reach
    extensions.duckdb.org, so an unbaked extension is one it can never have."""
    for ext in ("ducklake", "postgres"):
        assert f"'{ext}'" in AGENT_DOCKERFILE, ext


def test_agent_advertises_the_ducklake_extensions():
    """Dispatch is gated on the advertised set."""
    for ext in ("ducklake", "postgres"):
        assert f'"{ext}"' in CHANNEL, ext


def test_capability_matcher_expects_the_advertised_postgres_name():
    """The image installs `postgres`; the matcher must expect `postgres_scanner`."""
    from api.services.agent_capabilities import required_catalog_extensions

    assert "postgres_scanner" in required_catalog_extensions("ducklake")
    assert "postgres" not in required_catalog_extensions("ducklake")


# --- The DuckLake-only overlay ----------------------------------------------


class _ComposeLoader(yaml.SafeLoader):
    """Tolerates Compose's merge tags (`!override`, `!reset`), which are
    directives rather than data; we only need the value they carry."""


_ComposeLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: loader.construct_mapping(node, deep=True)
)

with (DEPLOY / "docker-compose.ducklake-only.yml").open() as f:
    DUCKLAKE_ONLY = yaml.load(f, Loader=_ComposeLoader)  # noqa: S506 - restricted loader


def test_ducklake_only_overlay_neutralises_polaris():
    """Compose cannot delete a service, so both are moved into a never-activated
    profile."""
    for service in ("polaris", "polaris-bootstrap"):
        assert DUCKLAKE_ONLY["services"][service]["profiles"] == ["iceberg"], service


def test_ducklake_only_overlay_drops_the_polaris_dependency():
    """`depends_on` is merged, not replaced, so restating the list would leave
    the inherited Polaris entry — a hard error for an inactive profile."""
    api = DUCKLAKE_ONLY["services"]["api"]
    assert "polaris" not in (api.get("depends_on") or {})
    assert "postgres" in (api.get("depends_on") or {})


def test_ducklake_only_overlay_turns_the_feature_on():
    """Without this the overlay could create no catalogs at all."""
    assert DUCKLAKE_ONLY["services"]["api"]["environment"]["DUCKLAKE_ENABLED"] == "true"
