from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://duckhaven:duckhaven@localhost:5432/duckhaven"
    # Apache Polaris (Iceberg REST catalog). The API authenticates to Polaris
    # with a service-principal client id/secret to create catalogs, namespaces
    # and tables. Dev defaults match the Polaris bootstrap root principal.
    polaris_base_url: str = "http://localhost:8181"
    polaris_realm: str = "POLARIS"
    polaris_client_id: str = "root"
    polaris_client_secret: str = "s3cr3t"
    # Polaris principal name the client credentials map to (grantee for catalog
    # data access). Defaults to the bootstrap root principal.
    polaris_principal: str = "root"
    polaris_http_timeout_s: float = 10.0
    # Bundled MinIO object store backing the object_store catalogs. `s3_endpoint`
    # is the externally-reachable URL Polaris vends to DuckDB agents (must be
    # reachable from the agent host); `s3_endpoint_internal` is what Polaris uses
    # to reach MinIO inside the compose network.
    s3_endpoint: str = "http://localhost:9000"
    s3_endpoint_internal: str = "http://minio:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "warehouse"
    secret_key: str = "change-me-in-production"
    session_max_age_seconds: int = 86400 * 7
    cors_origins: list[str] = ["http://localhost:5173"]
    cookie_secure: bool = True
    # Directory of the built SPA, served at / when present (baked into the image).
    static_dir: Path = Path("/app/static")
    # File holding the one-shot first-admin setup token, written by
    # deploy/api-entrypoint.sh on first boot and deleted by the API after the
    # first admin is created.
    setup_token_path: Path = Path("/var/duckhaven/setup_token")
    # Image self-hosters pull when running a new agent. Surfaced verbatim in
    # the add-agent compose snippet (admin UI).
    agent_image: str = "ghcr.io/tamasmrtn/duckhaven-agent:latest"
    # Single-use token the bundled agent exchanges for a session credential on
    # first registration. When set, the API seeds it on startup (folds in the
    # former agent-bootstrap one-shot); unset disables seeding.
    agent_bootstrap_token: str | None = None
    agent_bootstrap_ttl_hours: int = 240

    # Autonomous maintenance scanner. When enabled, a background loop in the API
    # lifespan periodically scans tables for health metrics. The cadence
    # (off/hourly/daily) is the runtime policy; this flag just gates the loop
    # itself, and the tick is how often the loop wakes to check whether a scan
    # is due. Single-scanner assumption: run one API replica with this enabled.
    maintenance_scanner_enabled: bool = True
    maintenance_scan_tick_s: float = 900.0
    # How often the expensive orphan/glob tier runs (cheap metadata runs every
    # due cycle); seconds. Default weekly.
    maintenance_deep_scan_interval_s: float = 7 * 86400.0

    # ── OIDC SSO (Part A) ─────────────────────────────────────────────────────
    # When enabled, the login page shows a "Sign in with SSO" button and the
    # /auth/oidc/* endpoints are live. Local accounts keep working regardless so
    # the break-glass admin is never locked out if the IdP is down.
    oidc_enabled: bool = False
    oidc_label: str = "SSO"
    # The IdP discovery document, e.g. https://idp.example.com/.well-known/openid-configuration
    oidc_server_metadata_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: str = "openid email profile groups"
    # Claim in the ID token holding the user's group memberships.
    oidc_groups_claim: str = "groups"
    # Maps IdP group value -> DuckHaven global role (e.g. {"dh-admins": "admin"}).
    # The highest-privilege matched role wins; unmatched users default to "user".
    oidc_group_role_map: dict[str, str] = {}
    # Public base URL the IdP redirects back to (scheme+host), used to build the
    # callback. When unset, derived from the incoming request.
    oidc_redirect_base_url: str | None = None

    # ── LDAP / Active Directory (Part A, secondary) ───────────────────────────
    ldap_enabled: bool = False
    ldap_server_uri: str | None = None  # ldaps://dc.example.com or ldap://...
    ldap_use_start_tls: bool = False
    ldap_bind_dn: str | None = None  # service account for search
    ldap_bind_password: str | None = None
    ldap_user_search_base: str | None = None
    ldap_user_filter: str = "(mail={email})"  # {email} is substituted, escaped
    ldap_email_attr: str = "mail"
    ldap_name_attr: str = "displayName"
    ldap_group_attr: str = "memberOf"
    # Maps a group DN -> DuckHaven global role.
    ldap_group_role_map: dict[str, str] = {}
    ldap_tls_ca_cert: str | None = None  # path to CA bundle for ldaps/STARTTLS
    ldap_timeout_s: float = 10.0


settings = Settings()
