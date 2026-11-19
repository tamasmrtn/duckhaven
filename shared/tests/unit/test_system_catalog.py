"""The connection options that carry the system catalog's row filter."""

import pytest

from duckhaven_shared.system_catalog import (
    SETTING_PRINCIPALS,
    SETTING_READABLE,
    SETTING_WORKSPACE,
    connection_options,
)

WS = "8f3c1f0e-5a0b-4c7e-9c3a-2d1e0f4b6a7c"
ANA = "0b6c2a7e-1d3f-4e5a-8b9c-0d1e2f3a4b5c"


def test_every_setting_is_set_in_a_fixed_order():
    options = connection_options(
        {
            SETTING_READABLE: "access.audit,query.history",
            SETTING_PRINCIPALS: ANA,
            SETTING_WORKSPACE: WS,
        }
    )

    assert options == (
        f"-c duckhaven.workspace={WS} -c duckhaven.principals={ANA} "
        "-c duckhaven.readable=access.audit,query.history"
    )


def test_a_missing_setting_is_set_empty_so_it_reads_as_nothing():
    assert connection_options({}) == (
        "-c duckhaven.workspace= -c duckhaven.principals= -c duckhaven.readable="
    )


@pytest.mark.parametrize(
    "value",
    [
        "access.audit -c duckhaven.readable=access.grants",
        "a'b",
        'a"b',
        "a\\b",
        "ACCESS.AUDIT",
        "access.audit\n",
    ],
)
def test_a_value_that_could_change_another_setting_is_refused(value):
    with pytest.raises(ValueError, match="Invalid value"):
        connection_options({SETTING_READABLE: value})


def test_any_other_setting_is_refused():
    with pytest.raises(ValueError, match="Unknown"):
        connection_options({"search_path": "access"})
