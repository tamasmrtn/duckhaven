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
    # OpenTelemetry tracing. Unset endpoint (the default) disables the SDK
    # entirely — no spans, no exporter, no instrumentation. Maps from the
    # standard OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_SERVICE_NAME env vars.
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "duckhaven-api"
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

    # Catalog storage-backend migration runner. When enabled, a background loop in
    # the API lifespan advances in-progress catalog migrations (copy + path-rewrite
    # + re-register Iceberg tables onto a new backend, then atomic cutover). Like
    # the scheduler/scanner it is coordinated across replicas by a Postgres
    # advisory lock, so it is safe to leave enabled on every replica. The tick is
    # how often the loop wakes to claim and advance a migration.
    migration_runner_enabled: bool = True
    migration_runner_tick_s: float = 30.0
    # How long the old backend's data is retained after a successful cutover before
    # the deferred cleanup sweep drops the source Polaris catalog. Gives a window
    # to roll back (reverse-migrate) if a problem surfaces post-cutover.
    migration_retention_days: int = 7

    # ── SQL sessions (dbt/dlt session layer) ──────────────────────────────────
    # The whole session surface — and thus the relaxed statement policy — is
    # OFF by default: an operator enables it only after deploying the hardened
    # agent (sandbox-before-relaxed-policy). Idle sessions are reaped after
    # sql_session_idle_timeout_s of inactivity; every session is force-closed at
    # sql_session_max_lifetime_s. The reaper is a leader-elected loop like the
    # scheduler/scanner. sql_session_open_timeout_s bounds how long the open
    # endpoint waits for the agent's SESSION_OPENED ack.
    sql_sessions_enabled: bool = False
    sql_session_idle_timeout_s: float = 900.0
    sql_session_max_lifetime_s: float = 14400.0
    sql_session_reaper_tick_s: float = 30.0
    sql_session_open_timeout_s: float = 30.0
    # Object-storage path segment for a session's scoped staging area
    # (<catalog root>/<segment>/<session_id>/); the statement policy confines
    # COPY/read_* to this prefix.
    sql_session_staging_prefix_segment: str = "_staging"

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

    # ── AI data assistant ─────────────────────────────────────────────────────
    # A governed, model-agnostic chat assistant that browses catalog metadata and
    # runs SQL as a service-account principal, through the same REST chokepoints as
    # any other client. Disabled by default; enable by pointing it at a service
    # account (created via the admin UI) and configuring a model.
    assistant_enabled: bool = False
    # Slug of the service account the assistant acts as. Its workspace memberships
    # and catalog grants govern the assistant's data access exactly like any
    # principal. The synthesized email is "{slug}@service-account.local".
    assistant_service_account_slug: str | None = None
    # Pydantic AI model string, e.g. "anthropic:claude-sonnet-4-latest",
    # "openai:gpt-4o", or "mistral:mistral-large-latest". No provider is assumed;
    # for OpenAI-compatible endpoints (Ollama, vLLM, Azure) set the base URL below
    # and use an "openai:<model>" string. Provider API keys come from the standard
    # provider env vars (ANTHROPIC_API_KEY/OPENAI_API_KEY/MISTRAL_API_KEY) unless
    # assistant_api_key is set.
    assistant_model: str = "anthropic:claude-sonnet-4-latest"
    # OpenAI-compatible base URL (Ollama/vLLM/Azure/…). When set, the model routes
    # through the OpenAI protocol against this endpoint (keyless self-hosted path).
    assistant_openai_base_url: str | None = None
    # Explicit API key for the configured model. Optional: hosted providers fall
    # back to their standard env var; keyless endpoints (Ollama) need nothing.
    assistant_api_key: str | None = None
    # Lifetime of the ephemeral PAT minted for each assistant turn's loopback
    # calls. It must comfortably exceed the longest plausible turn (bounded by
    # assistant_request_limit × per-query timeout), because the same token is used
    # for every loopback call in the turn; otherwise late calls would 401. A
    # crash-orphaned credential still expires harmlessly within this window.
    assistant_pat_ttl_s: int = 3600
    # Max concurrent assistant runs per API process, capping how much the shared
    # event loop can be occupied by (potentially slow) LLM turns.
    assistant_max_concurrency: int = 4
    # Hard cap on model requests within a single turn — stops a model stuck in a
    # tool loop from running queries and burning tokens indefinitely while holding
    # a concurrency slot. Enforced via Pydantic AI UsageLimits.
    assistant_request_limit: int = 20
    # Cap on model output tokens per turn — a coarse cost guard for self-hosters
    # bringing their own API keys.
    assistant_max_output_tokens: int = 4096
    # Coarse guard against unbounded history: only the most recent N turns are
    # replayed to the model, so per-turn cost stays bounded and a long conversation
    # doesn't eventually overflow the context window. Older turns are dropped.
    assistant_history_turn_cap: int = 40
    # Result-sample caps fed into model context (never the full Parquet payload).
    assistant_result_row_cap: int = 100
    assistant_result_byte_cap: int = 32_768
    # Include prompt/SQL/result content on assistant trace spans (gen_ai semconv).
    # Off means spans keep roles, token usage, tool names, timing, and status only.
    assistant_trace_include_content: bool = True

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
