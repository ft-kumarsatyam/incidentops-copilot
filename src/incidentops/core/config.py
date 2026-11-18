"""Runtime configuration, loaded from environment variables and an optional .env file."""

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Values that ship as development defaults. They are fine on a laptop and a hole anywhere else.
_PLACEHOLDER_PREFIXES = ("local-", "change-me", "change-this")
_SECRET_FIELDS = (
    "incidentops_api_key",
    "n8n_action_token",
    "zapier_action_token",
    "metrics_token",
    "webhook_signing_secret",
    "inbound_signing_secret",
)


class _SecretFileSource(PydanticBaseSettingsSource):
    """``NAME_FILE=/path`` fills NAME from that file (Docker/Kubernetes secrets, Vault agent).

    Lowest priority: an explicit ``NAME`` in the environment or .env wins. Scalar fields only.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, path in os.environ.items():
            if not name.upper().endswith("_FILE") or not path:
                continue
            field = name[: -len("_FILE")].lower()
            if field not in self.settings_cls.model_fields:
                continue
            try:
                values[field] = Path(path).read_text(encoding="utf-8").strip()
            except OSError as error:
                raise RuntimeError(f"{name}={path} could not be read: {error.strerror}") from error
        return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _SecretFileSource(settings_cls),
            file_secret_settings,
        )

    # Deployment
    incidentops_env: Literal["dev", "prod"] = "prod"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(default_factory=list)
    max_body_bytes: int = 256_000
    static_dir: Path | None = None  # built web UI; defaults to the packaged ./static
    rate_limit_per_minute: int = 120
    # How long a stopping server waits for open connections before closing them. Without a bound,
    # one open live-event stream (a browser tab) stops a --reload restart or a rolling deploy from
    # ever finishing, and the half-stopped server accepts connections it never answers.
    shutdown_timeout_seconds: int = 5
    # When set, the event stream and rate limiter are shared across replicas through Redis.
    redis_url: str | None = None
    redis_prefix: str = "incidentops"
    # Prometheus scrape endpoint at /metrics. Off by default; outside dev it needs METRICS_TOKEN.
    metrics_enabled: bool = False
    metrics_token: str | None = None

    # Auth and integrations
    # The legacy shared key. It maps to the admin role in the default tenant, and the caller may
    # name themselves with X-Actor. Prefer scoped keys (`incidentops keys create`) or OIDC.
    incidentops_api_key: str | None = None
    default_tenant: str = "default"
    # OIDC / SSO: set OIDC_ISSUER (and normally OIDC_AUDIENCE) to accept `Authorization: Bearer`
    # JWTs from your identity provider. Needs the `oidc` extra. Roles and tenant come from claims.
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None  # default: discovered from the issuer
    oidc_algorithms: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])
    oidc_roles_claim: str = "roles"  # dotted path allowed, e.g. realm_access.roles
    oidc_tenant_claim: str = "tenant_id"
    oidc_actor_claim: str = "sub"  # recorded in the audit log; e.g. email or preferred_username
    oidc_leeway_seconds: int = 30

    # Governance
    # Distinct approvers needed to approve an incident of a severity in four_eyes_severities.
    # Only meaningful with named identities (OIDC or scoped keys): the shared key cannot tell
    # two people apart, so it is refused for these approvals.
    four_eyes_approvals: int = 1
    four_eyes_severities: list[str] = Field(default_factory=lambda: ["SEV-1"])
    # When true, whoever opened an incident cannot also approve it.
    forbid_self_approval: bool = False
    # Delete audit entries older than this on `incidentops audit prune`. 0 keeps everything.
    audit_retention_days: int = 0
    # Also write a `read` audit entry for each incident a caller opens.
    audit_reads: bool = False
    github_token: str | None = None
    action_provider: Literal["n8n", "zapier"] = "n8n"
    n8n_action_webhook_url: str | None = None
    n8n_action_token: str | None = None  # shared secret n8n checks on the actions webhook
    # HMAC signing (replay-protected). Outbound signs dispatches to n8n/Zapier; inbound, when
    # set, requires POST /incidents to carry a valid signature in addition to the API key.
    webhook_signing_secret: str | None = None
    inbound_signing_secret: str | None = None
    signature_tolerance_seconds: int = 300
    # Hosts the action dispatcher may call. Empty means any host.
    outbound_allowed_hosts: list[str] = Field(default_factory=list)
    n8n_dispatch_attempts: int = 3
    n8n_dispatch_backoff: float = 0.5
    zapier_action_webhook_url: str | None = None
    zapier_action_token: str | None = None
    zapier_dispatch_attempts: int = 3
    zapier_dispatch_backoff: float = 0.5

    # Storage
    database_path: Path = Path(".data/incidentops.db")
    # When set, Postgres replaces SQLite for the read-model, audit log and LangGraph checkpoints.
    database_url: str | None = None
    database_pool_size: int = 10
    runbook_dir: Path = Path("runbooks")
    runbook_db: Path = Path(".data/runbooks.db")
    runbook_retrieval: Literal["fts", "hybrid"] = "fts"
    # Where the runbook index lives. "postgres" keeps it in DATABASE_URL (full-text search, plus
    # pgvector for hybrid) so every replica shares one index; "sqlite" is a local file per instance.
    runbook_store: Literal["sqlite", "postgres"] = "sqlite"
    max_runbook_results: int = 3
    auto_index_runbooks: bool = False

    # AI. "auto" uses Claude when ANTHROPIC_API_KEY is set, otherwise deterministic rules.
    ai_provider: Literal["auto", "claude", "rules"] = "auto"
    ai_topology: Literal["monolithic", "specialists"] = "monolithic"
    anthropic_api_key: str | None = None
    triage_model: str = "claude-sonnet-5-5"
    critic_model: str = "claude-haiku-5-5"
    specialist_model: str | None = None
    triage_effort: Literal["low", "medium", "high"] = "medium"
    critic_effort: Literal["low", "medium", "high"] = "low"
    ai_timeout_seconds: float = 45.0
    ai_max_output_tokens: int = 4000
    rolegraph_max_parallel: int = 4

    # Optional MCP read bridge during research (writes stay on n8n).
    mcp_enabled: bool = False
    mcp_servers: list[dict[str, str]] = Field(default_factory=list)

    # Execution. "sync" runs the analysis inside POST /incidents (the original behaviour).
    # "async" queues it: the API answers 202 and workers run it, with retries and a dead-letter
    # state. worker_concurrency is the number of in-process workers; 0 means the API only
    # queues and you run `incidentops worker` separately.
    execution_mode: Literal["sync", "async"] = "sync"
    worker_concurrency: int = 2
    worker_poll_seconds: float = 1.0
    job_lease_seconds: float = 60.0
    job_max_attempts: int = 3
    job_retry_backoff_seconds: float = 2.0
    run_timeout_seconds: float = 300.0
    max_wait_seconds: float = 30.0  # ceiling for "Prefer: wait=N" on POST /incidents

    # Workflow behaviour
    max_research_attempts: int = 2
    confidence_threshold: float = 0.8

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer)

    def placeholder_secrets(self) -> list[str]:
        """Names of settings still holding a development placeholder such as ``local-...``."""
        found = [
            name.upper()
            for name in _SECRET_FIELDS
            if str(getattr(self, name) or "").lower().startswith(_PLACEHOLDER_PREFIXES)
        ]
        if self.database_url and "local-" in self.database_url.lower():
            found.append("DATABASE_URL")
        return found

    def assert_production_ready(self) -> None:
        """Refuse to run outside dev with a development placeholder as a secret."""
        if self.incidentops_env == "dev":
            return
        bad = self.placeholder_secrets()
        if bad:
            raise RuntimeError(
                f"{', '.join(bad)} contain development placeholders (local-*, change-me). "
                "Set real secrets (or NAME_FILE=/run/secrets/name), or INCIDENTOPS_ENV=dev."
            )
