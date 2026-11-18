"""Runtime configuration, loaded from environment variables and an optional .env file."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Deployment
    incidentops_env: Literal["dev", "prod"] = "prod"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(default_factory=list)
    max_body_bytes: int = 256_000
    static_dir: Path | None = None  # built web UI; defaults to the packaged ./static
    rate_limit_per_minute: int = 120
    # When set, the event stream and rate limiter are shared across replicas through Redis.
    redis_url: str | None = None
    redis_prefix: str = "incidentops"

    # Auth and integrations
    incidentops_api_key: str | None = None
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

    # Workflow behaviour
    max_research_attempts: int = 2
    confidence_threshold: float = 0.8
