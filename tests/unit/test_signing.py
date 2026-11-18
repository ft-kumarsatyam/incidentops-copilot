import json
from typing import Any

import httpx
import pytest

from incidentops.core.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SignatureError,
    host_allowed,
    sign,
    signature_headers,
    verify,
)
from incidentops.tools.n8n import request_actions

BODY = b'{"run_id":"inc-1"}'
SECRET = "s3cret"


def _verify(headers: dict[str, str], *, body: bytes = BODY, now: float | None = None) -> None:
    verify(
        body,
        SECRET,
        timestamp=headers.get(TIMESTAMP_HEADER),
        signature=headers.get(SIGNATURE_HEADER),
        now=now,
    )


def test_a_fresh_signature_verifies() -> None:
    _verify(signature_headers(BODY, SECRET, now=1_000), now=1_100)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda h: {**h, SIGNATURE_HEADER: sign(BODY, "other", 1_000)}, "mismatch"),
        (lambda h: {k: v for k, v in h.items() if k != SIGNATURE_HEADER}, "missing"),
        (lambda h: {k: v for k, v in h.items() if k != TIMESTAMP_HEADER}, "missing"),
        (lambda h: {**h, TIMESTAMP_HEADER: "soon"}, "malformed"),
    ],
)
def test_bad_or_missing_signature_headers_are_rejected(mutate: Any, message: str) -> None:
    headers = mutate(signature_headers(BODY, SECRET, now=1_000))

    with pytest.raises(SignatureError, match=message):
        _verify(headers, now=1_000)


def test_a_tampered_body_is_rejected() -> None:
    headers = signature_headers(BODY, SECRET, now=1_000)

    with pytest.raises(SignatureError, match="mismatch"):
        _verify(headers, body=b'{"run_id":"inc-2"}', now=1_000)


def test_a_replayed_request_outside_the_window_is_rejected() -> None:
    headers = signature_headers(BODY, SECRET, now=1_000)

    with pytest.raises(SignatureError, match="window"):
        _verify(headers, now=1_000 + 301)


def test_a_changed_timestamp_invalidates_the_signature() -> None:
    headers = signature_headers(BODY, SECRET, now=1_000)
    headers[TIMESTAMP_HEADER] = "1050"

    with pytest.raises(SignatureError, match="mismatch"):
        _verify(headers, now=1_050)


def test_host_allowlist() -> None:
    assert host_allowed("https://anything.example/x", [])
    assert host_allowed("https://N8N.internal/webhook", ["n8n.internal"])
    assert not host_allowed("https://evil.example/webhook", ["n8n.internal"])
    assert not host_allowed("https://n8n.internal.evil.example/x", ["n8n.internal"])


class _Response:
    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]:
        return {"ok": True}


def test_dispatch_is_signed_when_a_secret_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> _Response:
        calls.append(kwargs)
        return _Response()

    monkeypatch.setattr(httpx, "post", post)
    payload = {"approval": "approved", "idempotency_key": "k"}

    request_actions("https://n8n", payload, signing_secret=SECRET)

    sent = calls[0]
    assert json.loads(sent["content"]) == payload
    verify(
        sent["content"],
        SECRET,
        timestamp=sent["headers"][TIMESTAMP_HEADER],
        signature=sent["headers"][SIGNATURE_HEADER],
    )


def test_dispatch_is_unsigned_without_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> _Response:
        calls.append(kwargs)
        return _Response()

    monkeypatch.setattr(httpx, "post", post)

    request_actions("https://n8n", {"approval": "approved", "idempotency_key": "k"})

    assert SIGNATURE_HEADER not in calls[0]["headers"]
