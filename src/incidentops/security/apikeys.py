"""Scoped API keys: ``iops_<id>_<secret>``, tied to one tenant and a set of roles.

The id is public and finds the row; the secret is shown once at creation and only its SHA-256 is
stored. Keys can expire and be revoked.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from incidentops.persistence.base import Repo
from incidentops.security.auth import AuthError
from incidentops.security.principal import ROLES, Principal

PREFIX = "iops_"
_ID_LENGTH = 12
_TOUCH_EVERY = timedelta(minutes=5)  # last_used_at is a hint, not a write per request


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _split(token: str) -> tuple[str, str]:
    rest = token[len(PREFIX) :]
    if len(rest) < _ID_LENGTH + 2 or rest[_ID_LENGTH] != "_":
        raise AuthError("malformed key")
    return rest[:_ID_LENGTH], rest[_ID_LENGTH + 1 :]


def issue_key(
    repo: Repo,
    *,
    name: str,
    tenant: str,
    roles: list[str],
    created_by: str,
    expires_days: int | None = None,
) -> tuple[dict[str, object], str]:
    """Create a key. Returns (its stored description, the token to hand over once)."""
    if not roles or any(role not in ROLES for role in roles):
        raise ValueError(f"roles must be a non-empty subset of {', '.join(ROLES)}")
    key_id, secret = secrets.token_hex(_ID_LENGTH // 2), secrets.token_urlsafe(32)
    expires = (
        (datetime.now(UTC) + timedelta(days=expires_days)).isoformat(timespec="milliseconds")
        if expires_days
        else None
    )
    repo.key_insert(key_id, name, _digest(secret), tenant, roles, created_by, expires)
    stored = repo.key_get(key_id)
    assert stored is not None
    stored.pop("secret_hash", None)
    return stored, f"{PREFIX}{key_id}_{secret}"


def resolve_key(repo: Repo, token: str) -> Principal:
    key_id, secret = _split(token)
    row = repo.key_get(key_id)
    # Compare even for an unknown id so the response time does not reveal which ids exist.
    expected = row["secret_hash"] if row else _digest("unknown")
    matches = hmac.compare_digest(_digest(secret), expected)
    if row is None or not matches:
        raise AuthError("unknown or invalid key")
    if row["revoked_at"]:
        raise AuthError("key revoked", key_id=key_id)
    now = datetime.now(UTC)
    if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= now:
        raise AuthError("key expired", key_id=key_id)
    last = row["last_used_at"]
    if not last or now - datetime.fromisoformat(last) > _TOUCH_EVERY:
        repo.key_touch(key_id)
    return Principal(
        subject=key_id,
        actor=f"key:{row['name']}",
        tenant=row["tenant"],
        roles=frozenset(row["roles"]),
        method="api-key",
    )
