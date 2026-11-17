"""Command-line entrypoint for local demos and n8n wrappers."""

import argparse
import json
from pathlib import Path
from typing import Any

from workflow_automation.config import Settings
from workflow_automation.workflows.incident import run_incident


def _json_default(value: Any) -> str:
    return str(value)


def check(settings: Settings) -> dict[str, Any]:
    return {
        "fixture_demo": "ready",
        "github_read": "configured" if settings.github_token else "not configured",
        "n8n_actions": "configured" if settings.n8n_action_webhook_url else "not configured",
        "checkpoint_dir": str(settings.checkpoint_dir),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow-run")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Check local and integration readiness")
    incident = commands.add_parser("incident", help="Run an IncidentOps fixture")
    incident.add_argument("--fixture", required=True, type=Path)
    incident.add_argument("--approval", choices=("approved", "rejected"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    if args.command == "check":
        result = check(settings)
    else:
        payload = json.loads(args.fixture.read_text(encoding="utf-8"))
        result = run_incident(payload, approval=args.approval, settings=settings)
    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))


if __name__ == "__main__":
    main()
