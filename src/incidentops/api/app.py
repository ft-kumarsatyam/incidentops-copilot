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
from incidentops.api.routes import admin, audit, decisions, health, incidents
from incidentops.api.routes import metrics as metrics_routes
from incidentops.core.config import Settings
from incidentops.core.logging import configure_logging, get_logger
from incidentops.core.metrics import Metrics
from incidentops.rag import index_runbooks_for_settings
from incidentops.security import Authenticator
from incidentops.security.oidc import OidcVerifier
from incidentops.workers import start_workers
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
    if settings.incidentops_env != "dev" and not (
        settings.incidentops_api_key or settings.oidc_enabled
    ):
        raise RuntimeError(
            "INCIDENTOPS_API_KEY or OIDC_ISSUER is required unless INCIDENTOPS_ENV=dev "
            "(refusing to start with an unauthenticated API)."
        )
    if settings.oidc_enabled and not settings.oidc_audience:
        raise RuntimeError(
            "OIDC_AUDIENCE is required with OIDC_ISSUER: without it any token the provider "
            "issued for any other application would be accepted."
        )
    settings.assert_production_ready()
    if (
        settings.metrics_enabled
        and settings.incidentops_env != "dev"
        and not settings.metrics_token
    ):
        raise RuntimeError(
            "METRICS_TOKEN is required to expose /metrics unless INCIDENTOPS_ENV=dev "
            "(refusing to publish metrics unauthenticated)."
        )
    metrics = Metrics(settings.metrics_enabled, version=__version__)
    if settings.auto_index_runbooks:
        index_runbooks_for_settings(settings)
    service = IncidentService(settings, metrics=metrics)
    oidc = OidcVerifier(settings) if settings.oidc_enabled else None
    authenticator = Authenticator(settings, service.repo, oidc)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        get_logger(__name__).info(
            "app.start", env=settings.incidentops_env, execution=settings.execution_mode
        )
        pool = start_workers(service)
        yield
        if pool is not None:
            pool.stop()
        await app.state.limiter.aclose()
        service.close()
        if oidc is not None:
            oidc.close()

    app = FastAPI(title="IncidentOps Copilot", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.service = service
    app.state.metrics = metrics
    app.state.authenticator = authenticator

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=[
                "Authorization",
                "X-API-Key",
                "X-Actor",
                "Content-Type",
                "Prefer",
                "X-Request-ID",
            ],
            expose_headers=["X-Total-Count", "X-Request-ID"],
        )
    install_middleware(app, settings, metrics)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(incidents.router)
    app.include_router(incidents.queue_router)
    app.include_router(audit.router)
    app.include_router(admin.router)
    app.include_router(decisions.router)
    if settings.metrics_enabled:
        app.include_router(metrics_routes.router)

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
