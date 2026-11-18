"""Command-line entrypoint for local demos, operations and n8n wrappers."""

import argparse
import json
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from incidentops.core.config import Settings
from incidentops.core.logging import configure_logging
from incidentops.persistence.factory import open_repo
from incidentops.rag import (
    build_runbook_retriever,
    index_runbooks_for_settings,
    runbook_index_status,
)
from incidentops.security.apikeys import issue_key
from incidentops.security.principal import ROLES
from incidentops.workers import build_pool
from incidentops.workflows.incident import IncidentService


def _json_default(value: Any) -> str:
    return str(value)


def check(settings: Settings) -> dict[str, Any]:
    return {
        "environment": settings.incidentops_env,
        "fixture_demo": "ready",
        "ai_topology": settings.ai_topology,
        "mcp_enabled": settings.mcp_enabled,
        "api_auth": (
            "configured"
            if settings.incidentops_api_key or settings.oidc_enabled
            else "not configured"
        ),
        "oidc": settings.oidc_issuer or "not configured",
        "placeholder_secrets": settings.placeholder_secrets() or "none",
        "github_read": "configured" if settings.github_token else "not configured",
        "action_provider": settings.action_provider,
        "n8n_actions": "configured" if settings.n8n_action_webhook_url else "not configured",
        "zapier_actions": (
            "configured" if settings.zapier_action_webhook_url else "not configured"
        ),
        "database": "postgres" if settings.database_url else str(settings.database_path),
        "runbook_index": "present" if runbook_index_status(settings) == "ok" else "missing",
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
    digest = commands.add_parser("digest", help="Summarize recent incidents (read-only)")
    digest.add_argument("--hours", type=int, default=24)
    worker = commands.add_parser(
        "worker",
        help="Run queued analyses (use with EXECUTION_MODE=async and WORKER_CONCURRENCY=0)",
    )
    worker.add_argument(
        "--concurrency", type=int, help="Parallel runs (default: WORKER_CONCURRENCY)"
    )
    worker.add_argument(
        "--grace", type=float, default=30.0, help="Seconds to let running jobs finish on shutdown"
    )
    queue = commands.add_parser("queue", help="Inspect or repair the analysis queue")
    queue_commands = queue.add_subparsers(dest="queue_command", required=True)
    queue_commands.add_parser("stats", help="Job counts and recently dead-lettered jobs")
    retry = queue_commands.add_parser("retry", help="Re-queue a run whose analysis failed")
    retry.add_argument("run_id")

    keys = commands.add_parser("keys", help="Manage scoped API keys")
    key_commands = keys.add_subparsers(dest="key_command", required=True)
    create = key_commands.add_parser("create", help="Create a key (the token is shown once)")
    create.add_argument("--name", required=True)
    create.add_argument("--roles", required=True, help=f"Comma-separated, from: {', '.join(ROLES)}")
    create.add_argument("--tenant", help="Default: DEFAULT_TENANT")
    create.add_argument("--expires-days", type=int)
    listing = key_commands.add_parser("list", help="List keys (never their secrets)")
    listing.add_argument("--tenant")
    revoke = key_commands.add_parser("revoke", help="Revoke a key by its id")
    revoke.add_argument("key_id")

    audit = commands.add_parser("audit", help="Verify, export or prune the audit log")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    verify = audit_commands.add_parser(
        "verify", help="Recompute the hash chain (exit status 1 if it was tampered with)"
    )
    verify.add_argument("--tenant", help="Default: every tenant")
    export = audit_commands.add_parser("export", help="Write the log as NDJSON")
    export.add_argument("--tenant", help="Default: every tenant")
    export.add_argument("--output", type=Path, help="Default: stdout")
    prune = audit_commands.add_parser("prune", help="Delete entries past the retention period")
    prune.add_argument("--days", type=int, help="Default: AUDIT_RETENTION_DAYS")
    prune.add_argument("--tenant", help="Default: every tenant")
    return parser


def run_keys(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    repo = open_repo(settings)
    try:
        tenant = args.tenant or settings.default_tenant if args.key_command == "create" else None
        if args.key_command == "create":
            roles = [role.strip() for role in args.roles.split(",") if role.strip()]
            stored, token = issue_key(
                repo,
                name=args.name,
                tenant=tenant or settings.default_tenant,
                roles=roles,
                created_by="cli",
                expires_days=args.expires_days,
            )
            repo.add_audit(
                "",
                "key:created",
                "cli",
                {"key_id": stored["id"], "name": args.name, "roles": roles},
                tenant=str(stored["tenant"]),
            )
            return {
                "key": stored,
                "token": token,
                "note": "Store the token now; it is not shown again.",
            }
        if args.key_command == "list":
            rows = repo.key_list(args.tenant)
            return {"keys": [{k: v for k, v in r.items() if k != "secret_hash"} for r in rows]}
        revoked = repo.key_revoke(args.key_id)
        if revoked:
            owner = (repo.key_get(args.key_id) or {}).get("tenant", settings.default_tenant)
            repo.add_audit("", "key:revoked", "cli", {"key_id": args.key_id}, tenant=owner)
        return {"revoked": revoked, "key_id": args.key_id}
    finally:
        repo.close()


def run_audit(settings: Settings, args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Returns (result, exit status). ``verify`` fails the process if any chain is broken."""
    repo = open_repo(settings)
    try:
        tenants = [args.tenant] if args.tenant else sorted(repo.audit_tenants())
        if args.audit_command == "verify":
            reports = [repo.audit_verify(name).as_dict() for name in tenants]
            ok = all(report["ok"] for report in reports)
            return {"ok": ok, "chains": reports}, 0 if ok else 1
        if args.audit_command == "prune":
            days = args.days if args.days is not None else settings.audit_retention_days
            if days <= 0:
                raise SystemExit("set --days or AUDIT_RETENTION_DAYS to a positive number")
            cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="milliseconds")
            removed = repo.audit_prune(cutoff, tenant=args.tenant)
            return {"removed": removed, "older_than": cutoff}, 0
        sink = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
        try:
            count = 0
            for name in tenants:
                offset = 0
                while True:
                    rows, _ = repo.audit_search(tenant=name, limit=500, offset=offset)
                    for row in rows:
                        sink.write(json.dumps({"type": "entry", **row}, default=str) + "\n")
                    count += len(rows)
                    if len(rows) < 500:
                        break
                    offset += 500
                report = repo.audit_verify(name).as_dict()
                sink.write(json.dumps({"type": "chain", **report}) + "\n")
        finally:
            if args.output:
                sink.close()
        return {"exported": count, "to": str(args.output) if args.output else "stdout"}, 0
    finally:
        repo.close()


def run_worker(
    settings: Settings,
    concurrency: int | None = None,
    grace: float = 30.0,
    stop: threading.Event | None = None,
) -> None:
    """Run workers in this process until told to stop (SIGINT/SIGTERM, or ``stop`` in tests)."""
    stop = stop or threading.Event()
    settings.assert_production_ready()
    service = IncidentService(settings)
    pool = build_pool(service, concurrency)
    if not settings.redis_url:
        print(
            "note: without REDIS_URL, status changes made here do not reach the API's live stream;"
            " clients see them on their next refresh.",
            flush=True,
        )
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
    pool.start()
    try:
        stop.wait()
    finally:
        pool.stop(timeout=grace)
        service.close()


def run_digest(settings: Settings, hours: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    repo = open_repo(settings)
    try:
        rows, _ = repo.search(limit=200)
    finally:
        repo.close()
    selected = [
        {
            "run_id": row["run_id"],
            "status": row.get("status"),
            "severity": row.get("severity"),
            "service": row.get("service"),
            "summary": row.get("summary"),
            "updated_at": row.get("updated_at"),
        }
        for row in rows
        if row.get("updated_at") and row["updated_at"] >= since.isoformat(timespec="milliseconds")
    ]
    return {"since_hours": hours, "count": len(selected), "incidents": selected}


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


def serve_options(settings: Settings, *, host: str, port: int, reload: bool) -> dict[str, Any]:
    """Keyword arguments for ``uvicorn.run``. Kept separate so a test can pin them."""
    return {
        "factory": True,
        "host": host,
        "port": port,
        "reload": reload,
        "timeout_graceful_shutdown": settings.shutdown_timeout_seconds,
    }


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "incidentops.api.app:create_app",
            **serve_options(settings, host=args.host, port=args.port, reload=args.reload),
        )
        return
    if args.command == "check":
        result = check(settings)
    elif args.command == "runbooks":
        if args.runbook_command == "index":
            stats = index_runbooks_for_settings(settings)
            where = "postgres" if settings.runbook_store == "postgres" else str(settings.runbook_db)
            result = {**stats, "database": where}
        else:
            retriever = build_runbook_retriever(settings)
            result = {
                "results": retriever.search(args.text, settings.max_runbook_results),
            }
    elif args.command == "digest":
        result = run_digest(settings, args.hours)
    elif args.command == "worker":
        run_worker(settings, args.concurrency, args.grace)
        return
    elif args.command == "keys":
        result = run_keys(settings, args)
    elif args.command == "audit":
        result, status = run_audit(settings, args)
        if args.audit_command == "export" and not args.output:
            return  # the NDJSON on stdout is the output
        print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))
        if status:
            sys.exit(status)
        return
    elif args.command == "queue":
        service = IncidentService(settings)
        try:
            if args.queue_command == "stats":
                result = service.queue_stats()
            else:
                result = service.retry_run(args.run_id, actor="cli")
        finally:
            service.close()
    else:
        result = run_fixture(settings, args.fixture, args.approval)
    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))


if __name__ == "__main__":
    main()
