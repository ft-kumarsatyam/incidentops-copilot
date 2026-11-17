"""Small HTTP boundary used by n8n."""

import hmac
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from workflow_automation.config import Settings
from workflow_automation.tools.n8n import request_actions
from workflow_automation.tools.runbooks import index_runbooks
from workflow_automation.workflows.incident import run_incident
from workflow_automation.workflows.incident.nodes import list_checkpoints, load_checkpoint


class IncidentRequest(BaseModel):
    run_id: str | None = None
    channel_id: str = Field(min_length=1)
    thread_ts: str = Field(min_length=1)
    reporter: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    service: str = ""
    owner: str = ""
    symptoms: list[str] = Field(default_factory=list)
    monitoring: dict[str, Any] = Field(default_factory=dict)
    github: dict[str, Any] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "__interrupt__"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if settings.auto_index_runbooks:
        index_runbooks(settings.runbook_dir, settings.runbook_db)
    app = FastAPI(title="IncidentOps Copilot", version="0.1.0")
    dashboard = Path(__file__).with_name("dashboard")
    app.mount("/assets", StaticFiles(directory=dashboard), name="dashboard-assets")

    def authenticate(x_api_key: str | None = Header(default=None)) -> None:
        expected = settings.incidentops_api_key
        if expected and (not x_api_key or not hmac.compare_digest(x_api_key, expected)):
            raise HTTPException(status_code=401, detail="invalid API key")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def dashboard_page() -> str:
        return (dashboard / "index.html").read_text(encoding="utf-8")

    @app.get("/incidents", dependencies=[Depends(authenticate)])
    def incidents() -> list[dict[str, Any]]:
        return [_public_state(dict(state)) for state in list_checkpoints(settings.checkpoint_dir)]

    @app.get("/incidents/{run_id}", dependencies=[Depends(authenticate)])
    def incident(run_id: str) -> dict[str, Any]:
        try:
            return _public_state(dict(load_checkpoint(run_id, settings.checkpoint_dir)))
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="run not found") from error

    @app.post("/incidents", dependencies=[Depends(authenticate)])
    def create_incident(request: IncidentRequest) -> dict[str, Any]:
        result = run_incident(
            request.model_dump(exclude_none=True),
            settings=settings,
        )
        return _public_state(result)

    @app.post("/incidents/{run_id}/decision", dependencies=[Depends(authenticate)])
    def decide(run_id: str, request: DecisionRequest) -> dict[str, Any]:
        try:
            state = load_checkpoint(run_id, settings.checkpoint_dir)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="run not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if request.idempotency_key != state.get("idempotency_key"):
            raise HTTPException(status_code=409, detail="idempotency key mismatch")
        result = run_incident(
            dict(state),
            approval=request.decision,
            settings=settings,
        )
        if request.decision == "approved" and settings.n8n_action_webhook_url:
            request_actions(settings.n8n_action_webhook_url, result)
            result["action_dispatch"] = "sent"
        elif request.decision == "approved":
            result["action_dispatch"] = "not_configured"
        return _public_state(result)

    return app


app = create_app()

