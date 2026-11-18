"""HMAC request signing with replay protection, shared by outbound and inbound webhooks.

The signed message is ``"{timestamp}.{body}"``. Receivers reject a timestamp outside the
tolerance window, so a captured request cannot be replayed later.
"""

import hashlib
import hmac
import time
from urllib.parse import urlsplit

TIMESTAMP_HEADER = "X-IncidentOps-Timestamp"
SIGNATURE_HEADER = "X-IncidentOps-Signature"
SCHEME = "v1"
DEFAULT_TOLERANCE_SECONDS = 300


class SignatureError(ValueError):
    """The signature is missing, malformed, stale or wrong."""


def sign(body: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"{SCHEME}={digest.hexdigest()}"


def signature_headers(body: bytes, secret: str, *, now: float | None = None) -> dict[str, str]:
    timestamp = int(time.time() if now is None else now)
    return {TIMESTAMP_HEADER: str(timestamp), SIGNATURE_HEADER: sign(body, secret, timestamp)}


def verify(
    body: bytes,
    secret: str,
    *,
    timestamp: str | None,
    signature: str | None,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Raise SignatureError unless the signature matches and the timestamp is fresh."""
    if not timestamp or not signature:
        raise SignatureError("missing signature headers")
    try:
        sent_at = int(timestamp)
    except ValueError as error:
        raise SignatureError("malformed timestamp") from error
    current = time.time() if now is None else now
    if abs(current - sent_at) > tolerance:
        raise SignatureError("timestamp outside the allowed window")
    if not hmac.compare_digest(signature, sign(body, secret, sent_at)):
        raise SignatureError("signature mismatch")


def host_allowed(url: str, allowed_hosts: list[str]) -> bool:
    """An empty allowlist allows everything. Entries match the host exactly, case-insensitively."""
    if not allowed_hosts:
        return True
    host = (urlsplit(url).hostname or "").lower()
    return host in {entry.strip().lower() for entry in allowed_hosts}
