"""End-to-end check of the n8n workflow in real n8n containers.

Starts two throwaway n8n instances that share one Redis, imports n8n/incident-ops.json into each
(with the Slack and GitHub URLs pointed at a local mock so nothing real is called), and checks:

  * Slack signatures: signed passes, forged/stale/unsigned is rejected, url_verification answers
  * the Redis claim: two instances fired at once run the GitHub step exactly once, a failed step
    releases the claim so the retry runs, and a finished action is never repeated

Needs docker, a reachable Redis (REDIS_URL, default redis://localhost:56379/0, the one
`make test-services`
starts) and the IncidentOps API on API_URL for the intake step (default http://localhost:8012).
Everything it starts is removed afterwards.

    python scripts/e2e_n8n.py
"""

import hashlib
import hmac
import http.server
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".data" / "e2e-n8n"
IMAGE = os.environ.get("N8N_IMAGE", "n8nio/n8n:2.49.0")
HOST = "host.docker.internal"
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:56379/0")
API_URL = os.environ.get("API_URL", "http://localhost:8012")
API_KEY = os.environ.get("API_KEY", "e2e-key")
MOCK_PORT = 9009
PORTS = {"a": 5681, "b": 5682}
SIGNING_SECRET = "e2e-slack-secret"
ACTION_TOKEN = "e2e-action-token"
RESULTS: list[tuple[str, bool, str]] = []


class Mock(http.server.BaseHTTPRequestHandler):
    calls: list[str] = []
    fail_next_github = 0
    delay = 0.0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)
        if "/issues" in self.path:
            time.sleep(Mock.delay)
            Mock.calls.append("github")
            if Mock.fail_next_github > 0:
                Mock.fail_next_github -= 1
                return self._reply(500, {"message": "boom"})
            return self._reply(201, {"number": 1})
        Mock.calls.append("slack")
        return self._reply(200, {"ok": True})

    def _reply(self, code: int, body: dict[str, object]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_: object) -> None:
        pass


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")


def post(port: int, path: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhook/{path}",
        data=body,
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def slack_headers(body: bytes, *, secret: str = SIGNING_SECRET, age: int = 0) -> dict[str, str]:
    stamp = str(int(time.time()) - age)
    sig = hmac.new(secret.encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256)
    return {"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": "v0=" + sig.hexdigest()}


def patched_workflow() -> Path:
    workflow = json.loads((ROOT / "n8n" / "incident-ops.json").read_text())
    workflow["id"] = "e2eworkflow0001"
    workflow["active"] = True
    for node in workflow["nodes"]:
        params = node["parameters"]
        if node["name"] == "Create GitHub Incident":
            params["url"] = f"http://{HOST}:{MOCK_PORT}/repos/o/r/issues"
        if node["name"] == "Send Email Status":
            node["disabled"] = True  # needs an SMTP credential the throwaway n8n does not have
        if node["name"] == "Post Approval to Slack":
            params["url"] = f"http://{HOST}:{MOCK_PORT}/slack"
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "wf.json"
    path.write_text(json.dumps(workflow))
    return path


def start(name: str, port: int) -> str:
    env = {
        "N8N_REDIS_URL": REDIS_URL.replace("localhost", HOST).replace("127.0.0.1", HOST),
        "NODE_FUNCTION_ALLOW_BUILTIN": "net,tls",
        "N8N_BLOCK_ENV_ACCESS_IN_NODE": "false",
        "N8N_ACTION_TOKEN": ACTION_TOKEN,
        "SLACK_SIGNING_SECRET": SIGNING_SECRET,
        "INCIDENTOPS_URL": API_URL.replace("localhost", HOST),
        "INCIDENTOPS_API_KEY": API_KEY,
        "INCIDENTOPS_PUBLIC_URL": "http://localhost:8000",
        "SLACK_BOT_TOKEN": "xoxb-test",
        "GITHUB_REPO": "o/r",
        "GITHUB_TOKEN": "x",
        "N8N_SECURE_COOKIE": "false",
        "N8N_DIAGNOSTICS_ENABLED": "false",
        "N8N_RUNNERS_ENABLED": os.environ.get("N8N_RUNNERS_ENABLED", "true"),
    }
    args = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--add-host",
        f"{HOST}:host-gateway",
        "-p",
        f"{port}:5678",
        "-v",
        f"{WORK}:/wf:ro",
        "--entrypoint",
        "/bin/sh",
    ]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    script = (
        "n8n import:workflow --input=/wf/wf.json && "
        "(n8n publish:workflow --id=e2eworkflow0001 "
        "|| n8n update:workflow --id=e2eworkflow0001 --active=true) && "
        "exec n8n start"
    )
    subprocess.run([*args, IMAGE, "-c", script], check=True, capture_output=True)
    return name


def wait_ready(port: int, name: str) -> None:
    for _ in range(90):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
            time.sleep(3)  # webhooks register just after the health check turns green
            return
        except OSError:
            time.sleep(1)
    logs = subprocess.run(["docker", "logs", "--tail", "40", name], capture_output=True, text=True)
    raise SystemExit(f"{name} did not start:\n{logs.stdout}{logs.stderr}")


def api_incident_count() -> int:
    request = urllib.request.Request(
        f"{API_URL}/incidents?limit=200", headers={"X-API-Key": API_KEY}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return int(response.headers.get("X-Total-Count", len(json.loads(response.read()))))


def action(port: int, key: str) -> tuple[int, dict[str, object] | str]:
    body = json.dumps(
        {
            "idempotency_key": key,
            "run_id": key,
            "summary": "x",
            "approved_actions": ["a"],
            "approval": "approved",
        }
    ).encode()
    status, text = post(
        port, "incidentops/actions", body, {"Authorization": f"Bearer {ACTION_TOKEN}"}
    )
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def main() -> int:
    server = http.server.ThreadingHTTPServer(("0.0.0.0", MOCK_PORT), Mock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    names = [f"ioe2e-n8n-{k}" for k in PORTS]
    try:
        patched_workflow()
        for name, port in zip(names, PORTS.values(), strict=True):
            start(name, port)
        for name, port in zip(names, PORTS.values(), strict=True):
            wait_ready(port, name)
        a, b = PORTS["a"], PORTS["b"]

        print("Slack signature")
        event = json.dumps(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "text": "checkout is throwing 5xx",
                    "channel": "C1",
                    "ts": "1.1",
                    "user": "U1",
                },
                "run_id": f"e2e-{int(time.time())}",
            }
        ).encode()
        before = api_incident_count()
        status, _ = post(a, "incidentops/intake", event, slack_headers(event))
        check(
            "signed intake is accepted and creates an incident",
            api_incident_count() == before + 1,
            f"http {status}",
        )
        before = api_incident_count()
        forged = json.dumps({"event": {"text": "forged"}, "run_id": "forged-1"}).encode()
        for label, headers in {
            "forged signature": slack_headers(forged, secret="wrong"),
            "stale timestamp": slack_headers(forged, age=3600),
            "no signature": {},
        }.items():
            status, _ = post(a, "incidentops/intake", forged, headers)
            check(f"{label} is rejected", status in (400, 401, 403, 500), f"http {status}")
        check("rejected requests created no incident", api_incident_count() == before)
        challenge = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
        status, text = post(a, "incidentops/intake", challenge, slack_headers(challenge))
        check(
            "url_verification is answered with the challenge",
            status == 200 and "abc123" in text,
            f"http {status}",
        )

        print("Redis claim across two n8n instances")
        Mock.calls.clear()
        Mock.delay = 1.5
        results: dict[str, tuple[int, object]] = {}
        threads = [
            threading.Thread(target=lambda p=p, n=n: results.__setitem__(n, action(p, "race-1")))
            for n, p in (("a", a), ("b", b))
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        github = Mock.calls.count("github")
        check("two instances at once call GitHub exactly once", github == 1, f"{github} calls")
        statuses = sorted(r[0] for r in results.values())
        check(
            "one succeeds, the other is told to retry",
            statuses[0] == 200 and statuses[1] >= 400,
            str(statuses),
        )
        Mock.delay = 0
        before = Mock.calls.count("github")
        status, body = action(b, "race-1")
        check(
            "a finished action is reported duplicate and not repeated",
            status == 200
            and isinstance(body, dict)
            and body.get("duplicate") is True
            and Mock.calls.count("github") == before,
            f"http {status} {body}",
        )

        print("Failure releases the claim")
        Mock.calls.clear()
        Mock.fail_next_github = 1
        status, _ = action(a, "fail-1")
        check("a failing GitHub call fails the request", status >= 400, f"http {status}")
        status, body = action(b, "fail-1")
        check(
            "the retry (even on the other instance) runs the action",
            status == 200
            and isinstance(body, dict)
            and body.get("executed") is True
            and Mock.calls.count("github") == 2,
            f"http {status} calls={Mock.calls.count('github')}",
        )
    finally:
        for name in names:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        server.shutdown()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
