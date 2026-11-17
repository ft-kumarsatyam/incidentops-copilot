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

PACKAGED_UI = Path(__file__).resolve().parents[1] / "static"

NO_UI = """<!doctype html><title>IncidentOps</title>
<body style="font:16px system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1>IncidentOps API is running</h1>
<p>The web UI is not built. For development run <code>make web-dev</code> (UI on :5173).
To serve it from this process run <code>make web-build</code>, or use Docker.</p>
<p>API docs: <a href="/docs">/docs</a></p></body>"""


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

    ui_dir = settings.static_dir or PACKAGED_UI
    if (ui_dir / "index.html").is_file():
        # Mounted last so every API route above takes precedence. The UI uses hash routing,
        # so no server-side history fallback is needed.
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def ui_not_built() -> str:
            return NO_UI

    return app
