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


def test_oidc_providers_blank_env_is_empty(monkeypatch):
    monkeypatch.setenv("OIDC_PROVIDERS", "")
    assert Settings().oidc_providers == []


def test_oidc_providers_parsed_from_json(monkeypatch):
    monkeypatch.setenv(
        "OIDC_PROVIDERS",
        '[{"id":"entra","label":"Microsoft","server_metadata_url":"https://idp/.well-known/openid-configuration",'
        '"client_id":"cid","client_secret":"sec"}]',
    )
    providers = Settings().oidc_providers
    assert [p.id for p in providers] == ["entra"]
    assert providers[0].label == "Microsoft"
    assert providers[0].scopes == "openid email profile"


def test_effective_providers_synthesizes_legacy_single(monkeypatch):
    """The single-provider env fields back-compat to one synthesized provider."""
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_LABEL", "Microsoft")
    monkeypatch.setenv("OIDC_SERVER_METADATA_URL", "https://idp/.well-known/openid-configuration")
    monkeypatch.setenv("OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "sec")
    effective = Settings().effective_oidc_providers()
    assert [p.id for p in effective] == ["sso"]
    assert effective[0].label == "Microsoft"


def test_explicit_providers_take_precedence_over_legacy(monkeypatch):
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv(
        "OIDC_SERVER_METADATA_URL", "https://legacy/.well-known/openid-configuration"
    )
    monkeypatch.setenv("OIDC_CLIENT_ID", "legacy")
    monkeypatch.setenv(
        "OIDC_PROVIDERS",
        '[{"id":"okta","label":"Okta","server_metadata_url":"https://okta/.well-known/openid-configuration",'
        '"client_id":"cid","client_secret":"sec"}]',
    )
    assert [p.id for p in Settings().effective_oidc_providers()] == ["okta"]
