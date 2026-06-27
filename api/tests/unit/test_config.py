"""Settings parsing edge cases."""

from api.config import Settings


def test_oidc_group_role_map_blank_env_is_empty(monkeypatch):
    """A blank OIDC_GROUP_ROLE_MAP (what compose passes when unset) must not
    crash boot — it coerces to an empty map rather than failing JSON decode."""
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", "")
    assert Settings().oidc_group_role_map == {}


def test_oidc_group_role_map_json_env_parses(monkeypatch):
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", '{"grp-1": "admin"}')
    assert Settings().oidc_group_role_map == {"grp-1": "admin"}
