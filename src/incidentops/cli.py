"""Command-line entrypoint for local demos, operations and n8n wrappers."""

import argparse
import json
from pathlib import Path
from typing import Any

from incidentops.core.config import Settings
from incidentops.core.logging import configure_logging
from incidentops.tools.runbooks import index_runbooks, search_runbooks
from incidentops.workflows.incident import IncidentService


def _json_default(value: Any) -> str:
    return str(value)


def check(settings: Settings) -> dict[str, Any]:
    return {
        "environment": settings.incidentops_env,
        "fixture_demo": "ready",
        "api_auth": "configured" if settings.incidentops_api_key else "not configured",
        "github_read": "configured" if settings.github_token else "not configured",
        "n8n_actions": "configured" if settings.n8n_action_webhook_url else "not configured",
        "database": str(settings.database_path),
        "runbook_index": "present" if settings.runbook_db.is_file() else "missing",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="incidentops")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Check local and integration readiness")
    serve = commands.add_parser("serve", help="Run the API and dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    incident = commands.add_parser("incident", help="Run an IncidentOps fixture")
    incident.add_argument("--fixture", required=True, type=Path)
    incident.add_argument("--approval", choices=("approved", "rejected"))
    runbooks = commands.add_parser("runbooks", help="Manage the local runbook index")
    runbook_commands = runbooks.add_subparsers(dest="runbook_command", required=True)
    runbook_commands.add_parser("index", help="Rebuild the SQLite FTS index")
    query = runbook_commands.add_parser("query", help="Search indexed runbooks")
    query.add_argument("text")
    return parser


def run_fixture(settings: Settings, fixture: Path, approval: str | None) -> dict[str, Any]:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    service = IncidentService(settings)
    try:
        state = service.start(payload, actor="cli")
        if approval and state["status"] == "awaiting_approval":
            state = service.decide(state["run_id"], state["idempotency_key"], approval, actor="cli")
        return state
    finally:
        service.close()


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "incidentops.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return
    if args.command == "check":
        result = check(settings)
    elif args.command == "runbooks":
        if args.runbook_command == "index":
            result = {
                "indexed_chunks": index_runbooks(settings.runbook_dir, settings.runbook_db),
                "database": str(settings.runbook_db),
            }
        else:
            result = {
                "results": search_runbooks(
                    settings.runbook_db,
                    args.text,
                    settings.max_runbook_results,
                )
            }
    else:
        result = run_fixture(settings, args.fixture, args.approval)
    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))


if __name__ == "__main__":
    main()
