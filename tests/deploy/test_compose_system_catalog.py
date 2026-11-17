"""The system catalog database's wiring in the compose file and its scripts.

Asserts the manifest and script text, as test_compose_ducklake.py does; that
Postgres enforces the lockdown is proved by
api/tests/integration/test_system_catalog_store.py against a live server.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"

with (DEPLOY / "docker-compose.yml").open() as f:
    DEV = yaml.safe_load(f)

INIT_SQL = (DEPLOY / "postgres-init" / "30-create-system-db.sh").read_text()
ENABLE_SQL = (ROOT / "scripts" / "enable-system-catalog.sh").read_text()


def test_public_cannot_reach_the_control_plane_or_create_in_the_system_database():
    """The reader login would otherwise inherit PUBLIC's CONNECT on `duckhaven`,
    which holds users, hashes and session tokens, and CREATE on `public`."""
    for script in (INIT_SQL, ENABLE_SQL):
        assert "REVOKE CONNECT ON DATABASE duckhaven FROM PUBLIC" in script
        assert "REVOKE CONNECT ON DATABASE polaris FROM PUBLIC" in script
        assert "REVOKE ALL ON DATABASE duckhaven_system FROM PUBLIC" in script
        assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in script


def test_init_and_enable_scripts_are_idempotent():
    for script in (INIT_SQL, ENABLE_SQL):
        assert (
            "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'duckhaven_system')"
            in script
        )


def test_the_scripts_create_no_reader_login():
    """The API creates it with a generated password; one made here would be a
    second, fixed credential."""
    for script in (INIT_SQL, ENABLE_SQL):
        assert "CREATE ROLE" not in script
        assert "PASSWORD" not in script


def test_the_init_script_runs_after_the_databases_it_locks_down():
    """initdb runs the scripts in name order, and the REVOKEs name `polaris`."""
    scripts = sorted(p.name for p in (DEPLOY / "postgres-init").glob("*.sh"))
    assert scripts.index("30-create-system-db.sh") > scripts.index("10-create-polaris-db.sh")


def test_the_api_reaches_the_system_database_as_owner():
    """It creates the reader login, so it needs CREATEROLE; agents never get
    this credential."""
    env = DEV["services"]["api"]["environment"]
    url = env["SYSTEM_CATALOG_DATABASE_URL"]
    assert "duckhaven:" in url
    assert url.rstrip("}").endswith("/duckhaven_system")
    assert env["SYSTEM_CATALOG_ENABLED"] == "${SYSTEM_CATALOG_ENABLED:-true}"


def test_backups_cover_the_system_database_when_it_exists():
    """DuckHaven rebuilds it, but the query history of a deleted workspace is
    only in the copy."""
    backup = (ROOT / "scripts" / "pg-backup.sh").read_text()
    assert "pg_database WHERE datname = 'duckhaven_system'" in backup
    assert 'databases="$databases duckhaven_system"' in backup
