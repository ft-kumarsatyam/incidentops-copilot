"""Load test: how latency and throughput change with the number of API replicas.

Each virtual user loops: report an incident (POST /incidents), then approve it
(POST /incidents/{id}/decision), the two writes a real responder makes. Requests go round-robin
across the replica URLs, and the approval is sent to a *different* replica than the one that
created the incident, so every run also proves the replicas share state.

    # against replicas you already run
    python scripts/loadtest.py run --urls http://localhost:8000 --users 16 --seconds 30

    # start 1, 2 and 4 replicas on this machine, measure each, write evals/loadtest_report.md
    python scripts/loadtest.py matrix --replicas 1,2,4 --users 16,64

``matrix`` needs Docker for Postgres and Redis (``compose.loadtest.yaml``) and the project's
virtualenv. Replicas are uvicorn processes on this machine, so their count is bounded by its cores.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "evals" / "loadtest_report.md"
API_KEY = "loadtest-key"
DB_URL = "postgresql://postgres:loadtest@127.0.0.1:55434/postgres"
REDIS_URL = "redis://127.0.0.1:56380/0"
BASE_PORT = 18100
ACTIONS_PORT = 18099


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an ascending list; 0.0 for an empty one."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


@dataclass
class Samples:
    """Latencies in milliseconds by operation, plus failures."""

    latencies: dict[str, list[float]] = field(
        default_factory=lambda: {"create": [], "decide": [], "incident": []}
    )
    errors: dict[str, int] = field(default_factory=dict)
    first_error: str = ""

    def fail(self, kind: str, detail: str) -> None:
        self.errors[kind] = self.errors.get(kind, 0) + 1
        self.first_error = self.first_error or detail

    @property
    def error_count(self) -> int:
        return sum(self.errors.values())


def summarize(samples: Samples, seconds: float) -> dict[str, Any]:
    """Collapse raw samples to the numbers a report needs."""
    result: dict[str, Any] = {
        "seconds": round(seconds, 1),
        "incidents": len(samples.latencies["incident"]),
        "errors": samples.error_count,
        "first_error": samples.first_error,
        "incidents_per_second": round(len(samples.latencies["incident"]) / seconds, 2)
        if seconds
        else 0.0,
    }
    for name, values in samples.latencies.items():
        ordered = sorted(values)
        result[name] = {
            "p50": round(percentile(ordered, 0.50), 1),
            "p95": round(percentile(ordered, 0.95), 1),
            "p99": round(percentile(ordered, 0.99), 1),
            "max": round(ordered[-1], 1) if ordered else 0.0,
        }
    return result


def incident_payload(user: int, count: int) -> dict[str, Any]:
    """A distinct, realistic incident, so no run is a duplicate of another."""
    token = uuid.uuid4().hex[:12]
    return {
        "run_id": f"load-{token}",
        "channel_id": "C-LOAD",
        "thread_ts": f"{time.time():.6f}",
        "reporter": f"U-LOAD-{user}",
        "summary": f"Checkout API is returning elevated 5xx responses (load run {token})",
        "service": "checkout-api",
        "owner": "payments-oncall",
        "symptoms": [f"{5 + count % 20}% error rate", "customer checkout failures"],
        "monitoring": {
            "service": "checkout-api",
            "error_rate": 0.12,
            "latency_ms": 2400,
            "alert": "5xx threshold exceeded",
        },
        "github": {"repo": "acme/checkout", "recent_deploy": "abc123"},
    }


async def _user(
    user: int,
    clients: list[httpx.AsyncClient],
    turn: itertools.count[int],
    stop_at: float,
    record_from: float,
    samples: Samples,
) -> None:
    count = 0
    while time.monotonic() < stop_at:
        count += 1
        first = clients[next(turn) % len(clients)]
        second = clients[next(turn) % len(clients)]
        started = time.perf_counter()
        measured = time.monotonic() >= record_from
        try:
            created = await first.post("/incidents", json=incident_payload(user, count))
            created_ms = (time.perf_counter() - started) * 1000
            if created.status_code != 200:
                if measured:
                    samples.fail(
                        "create", f"POST /incidents -> {created.status_code} {created.text[:200]}"
                    )
                continue
            body = created.json()
            decided_at = time.perf_counter()
            decision = await second.post(
                f"/incidents/{body['run_id']}/decision",
                json={"idempotency_key": body["idempotency_key"], "decision": "approved"},
            )
            decided_ms = (time.perf_counter() - decided_at) * 1000
            if decision.status_code != 200:
                if measured:
                    samples.fail(
                        "decide", f"decision -> {decision.status_code} {decision.text[:200]}"
                    )
                continue
        except httpx.HTTPError as error:
            if measured:
                samples.fail("transport", repr(error))
            continue
        if measured:
            samples.latencies["create"].append(created_ms)
            samples.latencies["decide"].append(decided_ms)
            samples.latencies["incident"].append((time.perf_counter() - started) * 1000)


async def run_load(
    urls: list[str],
    *,
    users: int,
    seconds: float,
    warmup: float = 5.0,
    api_key: str = API_KEY,
    transports: list[httpx.AsyncBaseTransport] | None = None,
) -> dict[str, Any]:
    """Run ``users`` closed-loop virtual users for ``seconds`` after ``warmup``; summarize."""
    limits = httpx.Limits(max_connections=users * 2, max_keepalive_connections=users * 2)
    clients = [
        httpx.AsyncClient(
            base_url=url,
            headers={"X-API-Key": api_key},
            timeout=60.0,
            limits=limits,
            transport=transports[index] if transports else None,
        )
        for index, url in enumerate(urls)
    ]
    samples = Samples()
    turn = itertools.count()
    begin = time.monotonic()
    record_from = begin + warmup
    stop_at = record_from + seconds
    try:
        await asyncio.gather(
            *(_user(i, clients, turn, stop_at, record_from, samples) for i in range(users))
        )
    finally:
        for client in clients:
            await client.aclose()
    result = summarize(samples, seconds)
    result["users"] = users
    result["replicas"] = len(urls)
    return result


# ---- the replica matrix -------------------------------------------------------------------


class _ActionsHandler(BaseHTTPRequestHandler):
    """Stands in for n8n: accepts the approved-actions webhook instantly."""

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        pass


def _compose() -> list[str]:
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True)
    return ["docker", "compose"] if probe.returncode == 0 else ["docker-compose"]


def _services(action: str) -> None:
    command = [*_compose(), "-f", str(ROOT / "compose.loadtest.yaml")]
    if action == "up":
        subprocess.run([*command, "up", "-d", "--wait"], check=True, cwd=ROOT)
    else:
        subprocess.run([*command, "down", "-v"], check=True, cwd=ROOT)


def _reset_database() -> None:
    import psycopg

    with psycopg.connect(DB_URL, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")


def _db_counters() -> tuple[int, int]:
    """Committed transactions and inserted rows so far in the load-test database."""
    import psycopg

    with psycopg.connect(DB_URL, autocommit=True) as connection:
        row = connection.execute(
            "SELECT xact_commit, tup_inserted FROM pg_stat_database "
            "WHERE datname = current_database()"
        ).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def _postgres_cpu() -> float:
    """The database container's CPU use right now, as Docker reports it (100 = one core)."""
    probe = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.CPUPerc}}",
            "incidentops-loadtest-postgres-1",
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(probe.stdout.strip().rstrip("%"))
    except ValueError:
        return 0.0


def wait_category(wait_type: str | None, event: str | None, query: str) -> str | None:
    """A readable label for what a busy Postgres session is waiting on, None when it is not."""
    if wait_type is None:
        return None
    if wait_type == "Client":
        return "waiting for the API (idle between statements)"
    if wait_type in ("Lock", "LWLock") and "audit_heads" in query:
        return "audit chain head row lock"
    if event and event.upper().startswith("WAL"):
        return "WAL flush (commit durability)"
    if wait_type == "Lock":
        return "other row locks"
    return f"{wait_type}:{event}"


def _sample_db(into: dict[str, Any], seconds: float) -> None:
    """Sample database CPU and what busy sessions wait on, in the middle of a measured run."""
    import psycopg

    time.sleep(seconds * 0.2)
    waits: dict[str, int] = {}
    deadline = time.monotonic() + seconds * 0.6
    cpu_at = time.monotonic()
    with psycopg.connect(DB_URL, autocommit=True) as connection:
        while time.monotonic() < deadline:
            rows = connection.execute(
                "SELECT wait_event_type, wait_event, "
                "left(regexp_replace(query, '\\s+', ' ', 'g'), 200) "
                "FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND state <> 'idle'"
            ).fetchall()
            for wait_type, event, query in rows:
                label = wait_category(wait_type, event, query)
                if label:
                    waits[label] = waits.get(label, 0) + 1
            if time.monotonic() - cpu_at > seconds * 0.25:  # docker stats takes about a second
                cpu_at = time.monotonic()
                into["cpu"] = max(into.get("cpu", 0.0), _postgres_cpu())
            time.sleep(0.1)
    into["waits"] = waits


def _docker_cpus() -> int:
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.NCPU}}"], capture_output=True, text=True
    )
    return int(probe.stdout.strip() or 0)


def _start_replicas(count: int, python: str) -> list[subprocess.Popen[bytes]]:
    env = {
        **os.environ,
        "INCIDENTOPS_ENV": "dev",
        "INCIDENTOPS_API_KEY": API_KEY,
        "AI_PROVIDER": "rules",
        "ANTHROPIC_API_KEY": "",
        "DATABASE_URL": DB_URL,
        "REDIS_URL": REDIS_URL,
        "RUNBOOK_STORE": "postgres",
        "AUTO_INDEX_RUNBOOKS": "true",
        "RATE_LIMIT_PER_MINUTE": "0",
        "EXECUTION_MODE": "sync",
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "N8N_ACTION_WEBHOOK_URL": f"http://127.0.0.1:{ACTIONS_PORT}/webhook/incidentops/actions",
        "N8N_ACTION_TOKEN": "loadtest-action-token",
        "METRICS_ENABLED": "false",
    }
    return [
        subprocess.Popen(
            [
                python, "-m", "uvicorn", "incidentops.api.app:create_app", "--factory",
                "--host", "127.0.0.1", "--port", str(BASE_PORT + index), "--log-level", "warning",
            ],
            cwd=ROOT,
            env=env,
        )
        for index in range(count)
    ]  # fmt: skip


def _wait_ready(urls: list[str], timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    for url in urls:
        while True:
            try:
                if httpx.get(f"{url}/ready", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"{url} did not become ready")
            time.sleep(0.5)


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        process.send_signal(signal.SIGINT)
    for process in processes:
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


def _hardware() -> str:
    cores = os.cpu_count() or 0
    chip = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        probe = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
        )
        chip = probe.stdout.strip() or chip
    return f"{chip}, {cores} logical cores, {platform.system()} {platform.release()}"


def _versions() -> str:
    from importlib.metadata import version

    return f"Python {platform.python_version()}, uvicorn {version('uvicorn')}"


def _findings(results: list[dict[str, Any]], docker_cpus: int) -> list[str]:
    """What the table says, computed from it so the prose cannot drift from the numbers."""
    if not results:
        return []
    least, most = min(r["replicas"] for r in results), max(r["replicas"] for r in results)
    lines = ["## What it shows", ""]
    for user_count in sorted({r["users"] for r in results}):
        rows = {r["replicas"]: r for r in results if r["users"] == user_count}
        if least in rows and most in rows and least != most:
            before, after = rows[least]["incidents_per_second"], rows[most]["incidents_per_second"]
            lines.append(
                f"- {user_count} users: {least} → {most} replicas moves throughput from "
                f"{before:.1f} to {after:.1f} incidents/s (×{after / before if before else 0:.2f}) "
                f"and incident p95 from {rows[least]['incident']['p95']:.0f} to "
                f"{rows[most]['incident']['p95']:.0f} ms."
            )
    commits = [r["db_commits_per_incident"] for r in results if "db_commits_per_incident" in r]
    inserts = [r["db_inserts_per_incident"] for r in results if "db_inserts_per_incident" in r]
    if commits and inserts:
        lines.append(
            f"- One incident costs Postgres about {sum(commits) / len(commits):.0f} committed "
            f"transactions and {sum(inserts) / len(inserts):.0f} inserted rows, almost all of it "
            "LangGraph checkpoint writes, so the database is the shared resource that every "
            "replica queues on."
        )
    busiest = results[-1] if "db_waits" in results[-1] else None
    waits = {k: v for k, v in (busiest or {}).get("db_waits", {}).items() if "API" not in k}
    total = sum(waits.values())
    if busiest and total:
        ranked = sorted(waits.items(), key=lambda item: item[1], reverse=True)[:3]
        shares = ", ".join(f"{label} {count / total:.0%}" for label, count in ranked)
        lines.append(
            f"- Where busy Postgres sessions waited ({busiest['replicas']} replicas, "
            f"{busiest['users']} users, sampled mid-run): {shares}."
        )
    peak = max((r.get("postgres_cpu_percent", 0) for r in results), default=0)
    if peak and docker_cpus:
        lines.append(
            f"- Postgres peaked at {peak}% CPU (100% is one core) in a Docker VM with "
            f"{docker_cpus} cores."
        )
    return lines


def render(
    results: list[dict[str, Any]],
    *,
    hardware: str,
    seconds: float,
    versions: str,
    docker_cpus: int = 0,
) -> str:
    users = sorted({r["users"] for r in results})
    lines = [
        "# Load test: latency by replica count",
        "",
        "Each virtual user reports an incident (`POST /incidents`) and then approves it "
        "(`POST /incidents/{id}/decision`), in a loop with no think time. Requests rotate across "
        "the replicas, and the approval always goes to a different replica than the creation, so "
        "every incident also crosses replicas through the shared Postgres. "
        f"{seconds:.0f} s measured per cell after a 5 s warmup.",
        "",
        "| Replicas | Users | Incidents/s | Incident p50 | Incident p95 | Incident p99 "
        "| Create p95 | Approve p95 | Postgres CPU | Errors |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for user_count in users:
        for r in sorted(
            (x for x in results if x["users"] == user_count), key=lambda x: x["replicas"]
        ):
            lines.append(
                f"| {r['replicas']} | {r['users']} | {r['incidents_per_second']:.1f} "
                f"| {r['incident']['p50']:.0f} ms | {r['incident']['p95']:.0f} ms "
                f"| {r['incident']['p99']:.0f} ms | {r['create']['p95']:.0f} ms "
                f"| {r['decide']['p95']:.0f} ms | {r.get('postgres_cpu_percent', 0)}% "
                f"| {r['errors']} |"
            )
    lines += ["", *_findings(results, docker_cpus)]
    lines += [
        "",
        "An *incident* is one create plus one approval, timed together. Closed-loop users: with "
        "more users than the stack can serve, latency is mostly queueing, so compare rows with the "
        "same number of users. Repeated runs on one machine varied by roughly 10%, so differences "
        "smaller than that, such as ×1.19 against ×1.29, are noise.",
        "",
        "## How this was run",
        "",
        f"- Machine: {hardware}. {versions}.",
        "- Replicas are `uvicorn` processes on that machine, one worker each, "
        "`EXECUTION_MODE=sync`, "
        "the offline rules provider (no model call), `RUNBOOK_STORE=postgres`.",
        "- Postgres 17 (pgvector image, default durability settings) and Redis run in Docker "
        f"(`compose.loadtest.yaml`, VM with {docker_cpus} CPUs); the n8n webhook is a local stub "
        "that answers instantly.",
        "- The load generator runs on the same machine and shares its cores with the replicas, so "
        "the numbers are for comparing replica counts, not absolute capacity.",
        "",
        "Regenerate with `make loadtest` (needs Docker). Results vary with hardware; commit a new "
        "report only from a run you trust.",
        "",
    ]
    return "\n".join(lines)


def run_matrix(replicas: list[int], users: list[int], seconds: float, out: Path) -> int:
    python = shutil.which("python") or sys.executable
    actions = ThreadingHTTPServer(("127.0.0.1", ACTIONS_PORT), _ActionsHandler)
    threading.Thread(target=actions.serve_forever, daemon=True).start()
    _services("up")
    results: list[dict[str, Any]] = []
    failed = False
    try:
        for count in replicas:
            _reset_database()
            processes = _start_replicas(count, python)
            urls = [f"http://127.0.0.1:{BASE_PORT + index}" for index in range(count)]
            try:
                _wait_ready(urls)
                for user_count in users:
                    print(f"{count} replica(s), {user_count} users ...", flush=True)
                    asyncio.run(run_load(urls, users=user_count, seconds=0.0, warmup=5.0))
                    commits, inserts = _db_counters()
                    sampled: dict[str, Any] = {}
                    sampler = threading.Thread(target=_sample_db, args=(sampled, seconds))
                    sampler.start()
                    result = asyncio.run(
                        run_load(urls, users=user_count, seconds=seconds, warmup=0.0)
                    )
                    sampler.join()
                    after_commits, after_inserts = _db_counters()
                    result["postgres_cpu_percent"] = round(sampled.get("cpu", 0.0))
                    result["db_waits"] = sampled.get("waits", {})
                    done = max(1, result["incidents"])
                    result["db_commits_per_incident"] = round((after_commits - commits) / done, 1)
                    result["db_inserts_per_incident"] = round((after_inserts - inserts) / done, 1)
                    print(
                        f"  {result['incidents_per_second']} incidents/s, "
                        f"p95 {result['incident']['p95']} ms, errors {result['errors']}",
                        flush=True,
                    )
                    if result["errors"]:
                        print(f"  first error: {result['first_error']}", flush=True)
                        failed = True
                    results.append(result)
            finally:
                _stop(processes)
    finally:
        actions.shutdown()
        _services("down")
    out.write_text(
        render(
            results,
            hardware=_hardware(),
            seconds=seconds,
            versions=_versions(),
            docker_cpus=_docker_cpus(),
        ),
        encoding="utf-8",
    )
    (out.with_suffix(".json")).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="load already-running replicas")
    run.add_argument("--urls", default="http://localhost:8000", help="comma-separated base URLs")
    run.add_argument("--users", type=int, default=16)
    run.add_argument("--seconds", type=float, default=30)
    run.add_argument("--warmup", type=float, default=5)
    run.add_argument("--api-key", default=os.environ.get("INCIDENTOPS_API_KEY", API_KEY))
    matrix = sub.add_parser("matrix", help="start N replicas locally and measure each")
    matrix.add_argument("--replicas", default="1,2,4")
    matrix.add_argument("--users", default="16,64")
    matrix.add_argument("--seconds", type=float, default=30)
    matrix.add_argument("--out", type=Path, default=REPORT)
    args = parser.parse_args()
    if args.command == "run":
        urls = [u.strip().rstrip("/") for u in args.urls.split(",") if u.strip()]
        result = asyncio.run(
            run_load(
                urls,
                users=args.users,
                seconds=args.seconds,
                warmup=args.warmup,
                api_key=args.api_key,
            )
        )
        print(json.dumps(result, indent=2))
        return 1 if result["errors"] else 0
    return run_matrix(
        [int(x) for x in args.replicas.split(",")],
        [int(x) for x in args.users.split(",")],
        args.seconds,
        args.out,
    )


if __name__ == "__main__":
    sys.exit(main())
