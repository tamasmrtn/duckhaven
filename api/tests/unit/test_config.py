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


def test_replica_identity_defaults_are_left_alone(monkeypatch):
    """A single-node deploy forwards to itself, i.e. behaves as one node. Nothing is
    derived unless asked for."""
    monkeypatch.delenv("REPLICA_ID", raising=False)
    monkeypatch.delenv("REPLICA_INTERNAL_URL", raising=False)
    settings = Settings()
    assert settings.replica_id == "api"
    assert settings.replica_internal_url == "http://localhost:8000"


def test_replica_identity_auto_uses_the_platform_replica_name(monkeypatch):
    """Container Apps gives every replica identical configuration, so a replica that
    cannot be told who it is reads the name the platform injected."""
    monkeypatch.setenv("REPLICA_ID", "auto")
    monkeypatch.setenv("CONTAINER_APP_REPLICA_NAME", "api--rev1-7b5cf89779-g8bsj")
    assert Settings().replica_id == "api--rev1-7b5cf89779-g8bsj"


def test_replica_identity_auto_falls_back_to_hostname(monkeypatch):
    """Off Container Apps there is no injected name, but the hostname is still distinct
    per replica."""
    import socket

    monkeypatch.setenv("REPLICA_ID", "auto")
    monkeypatch.delenv("CONTAINER_APP_REPLICA_NAME", raising=False)
    assert Settings().replica_id == socket.gethostname()


def test_replica_internal_url_auto_resolves_to_own_address(monkeypatch):
    """It has to be an address peers can reach directly. An ingress hostname would
    load-balance the forward back to an arbitrary replica, which is the bug this
    replaces."""
    import socket

    monkeypatch.setenv("REPLICA_INTERNAL_URL", "auto")
    expected = f"http://{socket.gethostbyname(socket.gethostname())}:8000"
    assert Settings().replica_internal_url == expected


def test_replica_identity_auto_yields_distinct_urls_per_host(monkeypatch):
    """The property the whole change rests on: two replicas must not record the same
    owner_url, or agent_dispatch treats a peer-owned agent as its own and gives up."""
    monkeypatch.setenv("REPLICA_INTERNAL_URL", "auto")

    monkeypatch.setattr("api.config.socket.gethostbyname", lambda _host: "10.42.0.4")
    first = Settings().replica_internal_url
    monkeypatch.setattr("api.config.socket.gethostbyname", lambda _host: "10.42.1.183")
    second = Settings().replica_internal_url

    assert first == "http://10.42.0.4:8000"
    assert second == "http://10.42.1.183:8000"
    assert first != second
