"""parse_set_concurrency: the worksheet concurrency control command."""

import pytest

from duckhaven_shared.concurrency import DEFAULT_PROFILE, parse_set_concurrency


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SET duckhaven_concurrency = 'single'", "single"),
        ("set duckhaven_concurrency='equal_2'", "equal_2"),
        ("  SET   duckhaven_concurrency = decaying_2 ;  ", "decaying_2"),
        ("SET duckhaven_concurrency = 'decaying_3'", "decaying_3"),
        ("SET duckhaven_concurrency = 'auto'", "auto"),
        ("RESET duckhaven_concurrency", DEFAULT_PROFILE),
    ],
)
def test_parses_command(sql, expected):
    assert parse_set_concurrency(sql) == expected


@pytest.mark.parametrize("sql", ["SELECT 1", "CREATE TABLE t (x INT)", "SET memory_limit='4GB'"])
def test_ignores_ordinary_sql(sql):
    assert parse_set_concurrency(sql) is None


def test_unknown_profile_raises():
    with pytest.raises(ValueError, match="Unknown concurrency profile"):
        parse_set_concurrency("SET duckhaven_concurrency = 'huge'")
