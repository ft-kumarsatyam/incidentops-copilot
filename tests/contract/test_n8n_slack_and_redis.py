"""The workflow's Slack signature check and Redis idempotency claim, run as real JavaScript.

n8n's globals ($json, $env, this.helpers, ...) are stubbed; everything else is the exact code that
ships in n8n/incident-ops.json, and the Redis tests talk to a real Redis.
"""

import hashlib
import hmac
import json
import shutil
import subprocess
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import require_module

WORKFLOW = Path(__file__).parents[2] / "n8n" / "incident-ops.json"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

PRELUDE = """
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const args = JSON.parse(process.argv[1]);
"""


def _nodes() -> dict[str, Any]:
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return {node["name"]: node for node in workflow["nodes"]}


def _code(name: str) -> str:
    code: str = _nodes()[name]["parameters"]["jsCode"]
    return code


def _node(script: str, payload: dict[str, Any]) -> Any:
    result = subprocess.run(
        ["node", "-e", PRELUDE + textwrap.dedent(script), json.dumps(payload)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


# --- Slack signature -------------------------------------------------------------------------

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
SLACK_RUN = """
const item = { json: { headers: args.headers }, binary: { data: {} } };
const ctx = { helpers: { getBinaryDataBuffer: async () => Buffer.from(args.raw, 'utf8') } };
new AsyncFunction('$input', '$env', args.code).call(ctx, { first: () => item }, args.env)
  .then((out) => console.log(JSON.stringify({ ok: out[0] === item })))
  .catch((e) => console.log(JSON.stringify({ error: e.message })));
"""


def _sign(secret: str, stamp: str, raw: str) -> str:
    digest = hmac.new(secret.encode(), f"v0:{stamp}:{raw}".encode(), hashlib.sha256).hexdigest()
    return f"v0={digest}"


def _slack(raw: str, headers: dict[str, str], secret: str | None = SECRET) -> dict[str, Any]:
    env = {} if secret is None else {"SLACK_SIGNING_SECRET": secret}
    out: dict[str, Any] = _node(
        SLACK_RUN,
        {"code": _code("Verify Slack Signature"), "raw": raw, "headers": headers, "env": env},
    )
    return out


def _headers(raw: str, *, secret: str = SECRET, stamp: int | None = None) -> dict[str, str]:
    stamp_text = str(int(time.time()) if stamp is None else stamp)
    return {
        "X-Slack-Request-Timestamp": stamp_text,  # n8n lowercases; the node must cope either way
        "X-Slack-Signature": _sign(secret, stamp_text, raw),
    }


BODY = json.dumps({"type": "event_callback", "event": {"type": "message", "text": "checkout 5xx"}})


def test_a_correctly_signed_request_passes() -> None:
    assert _slack(BODY, _headers(BODY)) == {"ok": True}


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("x", SECRET),
        ("é" * 5000 + "🔥", SECRET),  # long, multi-byte: many SHA-256 blocks
        (BODY, "k" * 100),  # key longer than the 64-byte block is hashed first
        ("", SECRET),
        ("a" * 55, SECRET),  # padding boundary cases
        ("a" * 56, SECRET),
        ("a" * 64, SECRET),
    ],
)
def test_the_javascript_hmac_matches_python(raw: str, secret: str) -> None:
    assert _slack(raw, _headers(raw, secret=secret), secret=secret) == {"ok": True}


def test_a_tampered_body_is_rejected() -> None:
    assert _slack(BODY + " ", _headers(BODY)) == {"error": "slack signature: invalid"}


def test_a_wrong_signing_secret_is_rejected() -> None:
    assert _slack(BODY, _headers(BODY, secret="not-the-secret")) == {
        "error": "slack signature: invalid"
    }


def test_a_missing_signature_is_rejected() -> None:
    headers = _headers(BODY)
    del headers["X-Slack-Signature"]
    assert _slack(BODY, headers) == {"error": "slack signature: invalid"}


@pytest.mark.parametrize("age", [330, 3600, -330])  # beyond the 300s window with margin
def test_a_stale_or_future_timestamp_is_rejected_even_if_the_signature_is_right(age: int) -> None:
    headers = _headers(BODY, stamp=int(time.time()) - age)
    assert _slack(BODY, headers) == {"error": "slack signature: missing or stale timestamp"}


@pytest.mark.parametrize("stamp", ["", "abc", "12 34", "1" * 20])
def test_a_malformed_timestamp_is_rejected(stamp: str) -> None:
    headers = {"x-slack-request-timestamp": stamp, "x-slack-signature": "v0=00"}
    assert _slack(BODY, headers) == {"error": "slack signature: missing or stale timestamp"}


def test_without_a_secret_the_check_is_off() -> None:
    assert _slack(BODY, {}, secret=None) == {"ok": True}


# --- Redis claim ------------------------------------------------------------------------------

GATE_SCRIPT = """
const run = (name, ctxJson, env, extra) => {
  const body = { idempotency_key: args.key, run_id: 'r1' };
  if (name === 'gate') {
    return new AsyncFunction('$json', '$env', '$getWorkflowStaticData', args.codes.gate)(
      { body }, env, () => ({}))
      .then((o) => ({ ...o[0].json, body: undefined }))
      .catch((e) => ({ error: e.message }));
  }
  const gate = { ...body, store: 'redis', claim: extra && extra.claim };
  const $ = () => ({ first: () => ({ json: gate }) });
  const $input = { first: () => ({ json: { error: { message: 'boom' } } }) };
  const fn = new AsyncFunction('$', '$input', '$env', '$getWorkflowStaticData', args.codes[name]);
  return fn($, $input, env, () => ({}))
    .then(() => ({ ok: true })).catch((e) => ({ error: e.message }));
};
(async () => {
  const env = { N8N_REDIS_URL: args.url };
  const results = {};
  for (const step of args.steps) {
    const [label, name, many, claimFrom] = step;
    const known = claimFrom && results[claimFrom] && results[claimFrom].claim;
    const claim = claimFrom ? (known ?? claimFrom) : undefined;
    results[label] = many
      ? await Promise.all(Array.from({ length: many }, () => run(name, null, env)))
      : await run(name, null, env, { claim });
  }
  console.log(JSON.stringify(results));
})();
"""


def _gate(url: str, key: str, steps: list[list[Any]]) -> dict[str, Any]:
    out: dict[str, Any] = _node(
        GATE_SCRIPT,
        {
            "url": url,
            "key": key,
            "steps": steps,
            "codes": {
                "gate": _code("Action Idempotency Gate"),
                "done": _code("Mark Action Done"),
                "release": _code("Release Action Claim"),
            },
        },
    )
    return out


@pytest.fixture
def key() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


def test_twenty_concurrent_instances_run_the_action_exactly_once(redis_url: str, key: str) -> None:
    out = _gate(redis_url, key, [["race", "gate", 20]])
    winners = [r for r in out["race"] if r.get("execute") is True]
    losers = [r for r in out["race"] if r.get("error")]
    assert len(winners) == 1
    assert len(losers) == 19
    assert {r["error"] for r in losers} == {"action already in progress on another instance"}


def test_a_failed_action_releases_its_claim_so_the_retry_can_run(redis_url: str, key: str) -> None:
    out = _gate(
        redis_url,
        key,
        [
            ["first", "gate", 0],
            ["released", "release", 0, "first"],
            ["retry", "gate", 0],
        ],
    )
    assert out["first"]["execute"] is True
    assert out["released"]["error"].startswith("action failed, claim released: boom")
    assert out["retry"]["execute"] is True  # not "duplicate", not "in progress"


def test_releasing_someone_elses_claim_does_nothing(redis_url: str, key: str) -> None:
    out = _gate(
        redis_url,
        key,
        [["first", "gate", 0], ["bogus", "release", 0, "nonexistent"], ["second", "gate", 0]],
    )
    assert out["first"]["execute"] is True
    assert out["second"] == {"error": "action already in progress on another instance"}


def test_a_finished_action_is_reported_as_a_duplicate_and_never_reruns(
    redis_url: str, key: str
) -> None:
    out = _gate(
        redis_url,
        key,
        [["first", "gate", 0], ["done", "done", 0, "first"], ["again", "gate", 0]],
    )
    assert out["again"]["duplicate"] is True
    assert out["again"]["execute"] is False


def test_the_claim_is_a_lease_not_a_permanent_lock(redis_url: str, key: str) -> None:
    require_module("redis")
    import redis

    _gate(redis_url, key, [["first", "gate", 0]])
    client = redis.Redis.from_url(redis_url)
    ttl = client.ttl(f"incidentops:n8n:action:{key}")
    assert 0 < ttl <= 600


def test_the_database_in_the_url_is_honoured(redis_url: str, key: str) -> None:
    require_module("redis")
    import redis

    url = redis_url.rsplit("/", 1)[0] + "/3"
    _gate(url, key, [["first", "gate", 0]])
    assert redis.Redis.from_url(url).exists(f"incidentops:n8n:action:{key}") == 1
    assert redis.Redis.from_url(redis_url).exists(f"incidentops:n8n:action:{key}") == 0


def test_an_unreachable_redis_fails_the_request_instead_of_running_unguarded(key: str) -> None:
    out = _gate("redis://127.0.0.1:1/0", key, [["first", "gate", 0]])
    assert "error" in out["first"]
    assert out["first"].get("execute") is not True


def test_without_a_redis_url_the_gate_falls_back_to_static_data(key: str) -> None:
    out = _node(
        """
        new AsyncFunction('$json', '$env', '$getWorkflowStaticData', args.code)(
          { body: { idempotency_key: args.key } }, {}, () => ({}))
          .then((o) => console.log(JSON.stringify(o[0].json)));
        """,
        {"code": _code("Action Idempotency Gate"), "key": key},
    )
    assert out["store"] == "static"
    assert out["execute"] is True


# --- workflow shape ---------------------------------------------------------------------------


def test_the_redis_helper_is_identical_in_every_node_that_embeds_it() -> None:
    helper = (
        (Path(__file__).parents[2] / "n8n" / "src" / "redis-helper.js").read_text().rstrip("\n")
    )
    for name in ("Action Idempotency Gate", "Mark Action Done", "Release Action Claim"):
        assert _code(name).startswith(helper), f"{name} has drifted from n8n/src/redis-helper.js"


def test_the_signature_is_verified_before_anything_reads_the_body() -> None:
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    conn = workflow["connections"]
    assert conn["Slack Incident Ingress"]["main"][0][0]["node"] == "Verify Slack Signature"
    assert conn["Verify Slack Signature"]["main"][0][0]["node"] == "Slack URL Check?"
    assert [e["node"] for e in conn["Slack URL Check?"]["main"][1]] == ["Normalize Incident"]
    assert _nodes()["Slack Incident Ingress"]["parameters"]["options"] == {"rawBody": True}


def test_failed_steps_release_the_claim() -> None:
    nodes = _nodes()
    conn = json.loads(WORKFLOW.read_text(encoding="utf-8"))["connections"]
    for name in ("Create GitHub Incident", "Send Email Status"):
        assert nodes[name]["onError"] == "continueErrorOutput"
        assert conn[name]["main"][1][0]["node"] == "Release Action Claim"


def test_webhooks_have_a_stable_id_so_the_documented_urls_work_after_any_import() -> None:
    """Without a webhookId, a CLI or API import serves /webhook/<workflowId>/<node>/<path>."""
    for node in _nodes().values():
        if node["type"] == "n8n-nodes-base.webhook":
            assert node.get("webhookId"), f"{node['name']} needs a webhookId"


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("redis://localhost:6379/0", True),
        ("redis://localhost", True),
        ("redis://:s3cret@cache.internal:6380/2", True),
        ("redis://user:p%40ss@[::1]:6379/1", True),
        ("rediss://cache.example.com:6380/0", True),
        ("http://nope", False),
        ("localhost:6379", False),
    ],
)
def test_the_redis_url_forms_are_parsed_without_the_url_global(url: str, ok: bool) -> None:
    """n8n's Code-node sandbox has no URL global, so the helper parses the URL by hand."""
    out = _node(
        """
        const head = args.code.split('function redisEncode')[0];
        const code = head.replace(/^const (net|tls) = .*$/gm, '');
        // Run it with no URL global, like the n8n sandbox does.
        const call = '; return redisConfig(' + JSON.stringify(args.url) + ');';
        const run = new Function('URL', code + call);
        try { console.log(JSON.stringify({ cfg: run(undefined) })); }
        catch (e) { console.log(JSON.stringify({ error: e.message })); }
        """,
        {"code": _code("Action Idempotency Gate"), "url": url},
    )
    assert ("cfg" in out) is ok
    if ok:
        assert out["cfg"]["port"] in (6379, 6380)
