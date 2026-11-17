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

    # Auth and integrations
    incidentops_api_key: str | None = None
    github_token: str | None = None
    action_provider: Literal["n8n", "zapier"] = "n8n"
    n8n_action_webhook_url: str | None = None
    n8n_action_token: str | None = None  # shared secret n8n checks on the actions webhook
    n8n_dispatch_attempts: int = 3
    n8n_dispatch_backoff: float = 0.5
    zapier_action_webhook_url: str | None = None
    zapier_action_token: str | None = None
    zapier_dispatch_attempts: int = 3
    zapier_dispatch_backoff: float = 0.5

    # Storage
    database_path: Path = Path(".data/incidentops.db")
    runbook_dir: Path = Path("runbooks")
    runbook_db: Path = Path(".data/runbooks.db")
    runbook_retrieval: Literal["fts", "hybrid"] = "fts"
    max_runbook_results: int = 3
    auto_index_runbooks: bool = False

    # AI. "auto" uses Claude when ANTHROPIC_API_KEY is set, otherwise deterministic rules.
    ai_provider: Literal["auto", "claude", "rules"] = "auto"
    anthropic_api_key: str | None = None
    triage_model: str = "claude-sonnet-5-5"
    critic_model: str = "claude-haiku-5-5"
    triage_effort: Literal["low", "medium", "high"] = "medium"
    critic_effort: Literal["low", "medium", "high"] = "low"
    ai_timeout_seconds: float = 45.0
    ai_max_output_tokens: int = 4000

    # Workflow behaviour
    max_research_attempts: int = 2
    confidence_threshold: float = 0.8
