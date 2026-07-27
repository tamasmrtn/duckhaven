"""Identifier handling in the Entra login-role bootstrap.

The connection paths need a live Azure Postgres, so what is covered here is the
part that can go wrong silently: names reaching SQL text, and the environment
parsing that decides which roles get created at all.
"""

import pytest

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
