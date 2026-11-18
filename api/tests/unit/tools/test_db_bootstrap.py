"""Identifier handling in the Entra login-role bootstrap.

The connection paths need a live Azure Postgres, so what is covered here is the
part that can go wrong silently: names reaching SQL text, and the environment
parsing that decides which roles get created at all.
"""

import pytest

from api.tools import db_bootstrap
from api.tools.db_bootstrap import _names, _quote


def test_quotes_azure_resource_names():
    # Identity names contain hyphens, so they cannot appear unquoted.
    assert _quote("id-duckhaven-api-prod") == '"id-duckhaven-api-prod"'


@pytest.mark.parametrize(
    "name",
    ['api"; DROP DATABASE duckhaven; --', "api role", "api;", ""],
)
def test_rejects_anything_that_is_not_a_plain_name(name):
    """These identifiers are interpolated into GRANT statements, so a name that
    would need escaping is refused outright rather than quoted around."""
    with pytest.raises(ValueError):
        _quote(name)


def test_names_splits_and_strips(monkeypatch):
    monkeypatch.setenv("DB_BOOTSTRAP_PRINCIPALS", " id-api , id-polaris ")
    assert _names("DB_BOOTSTRAP_PRINCIPALS") == ["id-api", "id-polaris"]


def test_names_of_unset_or_empty_is_empty(monkeypatch):
    """main() treats an empty list as a misconfiguration and exits non-zero, so
    this must not silently become [""]."""
    monkeypatch.delenv("DB_BOOTSTRAP_DATABASES", raising=False)
    assert _names("DB_BOOTSTRAP_DATABASES") == []

    monkeypatch.setenv("DB_BOOTSTRAP_DATABASES", " , ")
    assert _names("DB_BOOTSTRAP_DATABASES") == []


class _Conn:
    """Records the statements one connection runs."""

    def __init__(self, log: list[tuple[str, str]], database: str) -> None:
        self.log, self.database = log, database

    async def fetchval(self, sql: str, *args):
        # The roles exist already, so no principal is created.
        return 1

    async def execute(self, sql: str) -> None:
        self.log.append((self.database, sql))

    async def close(self) -> None:
        pass


@pytest.fixture
def run(monkeypatch):
    """main() against recording connections; returns what each database ran."""
    import azure.identity

    class _Credential:
        def get_token(self, scope):
            return type("Token", (), {"token": "t"})()

    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", _Credential)
    monkeypatch.setenv("DB_BOOTSTRAP_HOST", "pg.example")
    monkeypatch.setenv("DB_BOOTSTRAP_USER", "id-dbadmin")
    monkeypatch.setenv("DB_BOOTSTRAP_PRINCIPALS", "id-api")
    monkeypatch.setenv("DB_BOOTSTRAP_DATABASES", "duckhaven")
    monkeypatch.delenv("DB_BOOTSTRAP_SYSTEM_DATABASE", raising=False)

    async def go() -> list[tuple[str, str]]:
        log: list[tuple[str, str]] = []

        async def connect(host, user, database, token):
            return _Conn(log, database)

        monkeypatch.setattr(db_bootstrap, "_connect", connect)
        assert await db_bootstrap.main() == 0
        return log

    return go


async def test_without_a_system_database_the_grants_are_unchanged(run):
    log = await run()

    assert [db for db, _ in log] == ["duckhaven"] * 4
    assert not any("CREATEROLE" in sql or "PUBLIC" in sql for _, sql in log)


async def test_a_system_database_lets_the_api_keep_it_and_no_one_else_reach_it(run, monkeypatch):
    monkeypatch.setenv("DB_BOOTSTRAP_SYSTEM_DATABASE", "duckhaven_system")

    log = await run()

    assert ("postgres", 'ALTER ROLE "id-api" CREATEROLE') in log
    control = [sql for db, sql in log if db == "duckhaven"]
    # Its own grant first, so a re-run can still connect once PUBLIC cannot.
    assert control[-2:] == [
        'GRANT CONNECT ON DATABASE "duckhaven" TO CURRENT_USER',
        'REVOKE CONNECT ON DATABASE "duckhaven" FROM PUBLIC',
    ]
    assert [sql for db, sql in log if db == "duckhaven_system"] == [
        'REVOKE ALL ON DATABASE "duckhaven_system" FROM PUBLIC',
        "REVOKE ALL ON SCHEMA public FROM PUBLIC",
        'GRANT CONNECT, CREATE ON DATABASE "duckhaven_system" TO "id-api" WITH GRANT OPTION',
    ]
