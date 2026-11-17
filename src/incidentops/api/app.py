"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from incidentops import __version__
from incidentops.api.errors import install_error_handlers
from incidentops.api.middleware import install_middleware
from incidentops.api.routes import health, incidents
from incidentops.core.config import Settings
from incidentops.core.logging import configure_logging, get_logger
from incidentops.tools.runbooks import index_runbooks
from incidentops.workflows.incident.service import IncidentService

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    if settings.incidentops_env != "dev" and not settings.incidentops_api_key:
        raise RuntimeError(
            "INCIDENTOPS_API_KEY is required unless INCIDENTOPS_ENV=dev "
            "(refusing to start with an unauthenticated API)."
        )
    if settings.auto_index_runbooks:
        index_runbooks(settings.runbook_dir, settings.runbook_db)
    service = IncidentService(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        get_logger(__name__).info("app.start", env=settings.incidentops_env)
        yield
        service.close()

    app = FastAPI(title="IncidentOps Copilot", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.service = service

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["X-API-Key", "X-Actor", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Total-Count", "X-Request-ID"],
        )
    install_middleware(app, settings)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(incidents.router)

    app.mount("/assets", StaticFiles(directory=DASHBOARD), name="dashboard-assets")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard_page() -> str:
        return (DASHBOARD / "index.html").read_text(encoding="utf-8")

    return app
