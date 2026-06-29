import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class OidcProvider(BaseModel):
    """One OpenID Connect identity provider the login page can offer.

    Every supported IdP (Microsoft Entra, Google, Okta, Authentik, Keycloak,
    AWS Cognito, …) speaks standard OIDC, so a provider is just a discovery URL
    plus a confidential client — no provider-specific code. ``id`` is a URL-safe
    slug used in the per-provider callback path ``/api/auth/oidc/{id}/callback``.
    """

    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    label: str
    server_metadata_url: str
    client_id: str
    client_secret: str
    scopes: str = "openid email profile"
    groups_claim: str = "groups"
    group_role_map: dict[str, str] = {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://duckhaven:duckhaven@localhost:5432/duckhaven"
    # Root log level. uvicorn configures only its own loggers (and leaves the root
    # logger handler-less), so without this the API's module loggers — scanner
    # leadership, cross-replica dispatch warnings, Polaris errors — are dropped.
    log_level: str = "INFO"
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
    # Coordinated across replicas by a Postgres advisory lock (leader election),
    # so it is safe to leave enabled on every replica: only the lock holder runs
    # a scan cycle each tick. This flag still gates whether *this* replica
    # participates at all.
    maintenance_scanner_enabled: bool = True
    maintenance_scan_tick_s: float = 900.0
    # How often the expensive orphan/glob tier runs (cheap metadata runs every
    # due cycle); seconds. Default weekly.
    maintenance_deep_scan_interval_s: float = 7 * 86400.0

    # Job scheduler. When enabled, a background loop in the API lifespan runs saved
    # queries on their cron schedules. Like the maintenance scanner it is
    # coordinated across replicas by a Postgres advisory lock (leader election), so
    # it is safe to leave enabled on every replica: only the lock holder dispatches
    # each tick. The tick is how often the loop wakes to check for due schedules,
    # and so also the finest effective cadence (60s => at most once per minute).
    scheduler_enabled: bool = True
    scheduler_tick_s: float = 60.0

    # ── High availability (multi-replica control plane) ───────────────────────
    # Identity of this API replica and the URL peers use to reach it for
    # inter-replica agent-dispatch forwarding. The defaults make a single-replica
    # deploy forward to itself, i.e. behave exactly as a single node.
    replica_id: str = "api"
    replica_internal_url: str = "http://localhost:8000"
    # Shared secret guarding the network-private /internal/* forwarding endpoints.
    # When unset, peer forwarding is disabled (single-replica mode); an agent on
    # another replica is then simply treated as unreachable.
    internal_api_secret: str | None = None
    # An agent counts as connected cluster-wide if it owns a replica AND has
    # pinged within this window. The TTL covers a replica that died without
    # clearing its ownership rows.
    agent_presence_ttl_s: float = 90.0
    # SQLAlchemy connection pool. pool_pre_ping discards connections to a failed
    # Postgres primary after failover so the app reconnects transparently; the
    # sizing bounds per-replica connections against the Postgres max.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_s: int = 1800

    # Prometheus metrics exposition at GET /api/metrics. Unauthenticated like the
    # health endpoints (Prometheus scrapers carry no session cookie); keep it on
    # the internal network. Set false to remove the endpoint entirely.
    metrics_enabled: bool = True

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
    # NoDecode: parse the env value ourselves (see the validator) so a blank
    # passthrough value coerces to {} instead of failing JSON decoding on boot.
    oidc_group_role_map: Annotated[dict[str, str], NoDecode] = {}
    # Public base URL the IdP redirects back to (scheme+host), used to build the
    # callback. When unset, derived from the incoming request.
    oidc_redirect_base_url: str | None = None
    # Multiple OIDC providers as a JSON list (each a button on the login page).
    # Takes precedence over the single-provider fields above; see
    # effective_oidc_providers(). Example:
    # OIDC_PROVIDERS=[{"id":"entra","label":"Microsoft","server_metadata_url":"…",
    #   "client_id":"…","client_secret":"…"}]
    oidc_providers: Annotated[list[OidcProvider], NoDecode] = []

    @field_validator("oidc_providers", mode="before")
    @classmethod
    def _parse_oidc_providers(cls, v: object) -> object:
        """Parse the provider list from its env string (NoDecode); blank -> []."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return []
        if isinstance(v, str):
            return json.loads(v)
        return v

    def effective_oidc_providers(self) -> list[OidcProvider]:
        """The providers to offer: the explicit list, or the single-provider
        fields synthesized into one provider (id ``sso``) for back-compat."""
        if self.oidc_providers:
            return self.oidc_providers
        if self.oidc_enabled and self.oidc_server_metadata_url and self.oidc_client_id:
            return [
                OidcProvider(
                    id="sso",
                    label=self.oidc_label,
                    server_metadata_url=self.oidc_server_metadata_url,
                    client_id=self.oidc_client_id,
                    client_secret=self.oidc_client_secret or "",
                    scopes=self.oidc_scopes,
                    groups_claim=self.oidc_groups_claim,
                    group_role_map=self.oidc_group_role_map,
                )
            ]
        return []

    @field_validator("oidc_group_role_map", mode="before")
    @classmethod
    def _parse_group_role_map(cls, v: object) -> object:
        """Parse the group->role map from its env string ourselves (NoDecode).

        Compose passes ``OIDC_GROUP_ROLE_MAP`` through as an empty string when an
        operator enables SSO without a group map; an empty string is not valid
        JSON, so coerce blank to ``{}`` and JSON-decode anything else, rather
        than crashing on boot."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return {}
        if isinstance(v, str):
            return json.loads(v)
        return v

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
