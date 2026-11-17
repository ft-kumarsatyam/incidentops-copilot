"""Runtime configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    github_token: str | None = None
    n8n_action_webhook_url: str | None = None
    incidentops_api_key: str | None = None
    checkpoint_dir: Path = Path(".checkpoints")
    runbook_dir: Path = Path("runbooks")
    runbook_db: Path = Path(".data/runbooks.db")
    max_runbook_results: int = 3
    auto_index_runbooks: bool = False
    max_research_attempts: int = 2
    confidence_threshold: float = 0.8
