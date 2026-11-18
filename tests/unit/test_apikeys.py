"""Scoped API keys: issue, resolve, expire, revoke. Both backends."""

from datetime import UTC, datetime, timedelta

import pytest

from incidentops.persistence.base import Repo
from incidentops.security import AuthError
from incidentops.security.apikeys import PREFIX, issue_key, resolve_key


def _issue(repo: Repo, **overrides: object) -> tuple[dict[str, object], str]:
    options: dict[str, object] = {
        "name": "alerting",
        "tenant": "acme",
        "roles": ["responder"],
        "created_by": "admin",
    }
    return issue_key(repo, **{**options, **overrides})  # type: ignore[arg-type]


def test_a_key_resolves_to_a_named_principal(repo: Repo) -> None:
    stored, token = _issue(repo)

    principal = resolve_key(repo, token)

    assert token.startswith(PREFIX)
    assert principal.method == "api-key" and principal.named
    assert principal.tenant == "acme" and principal.roles == {"responder"}
    assert principal.actor == "key:alerting" and principal.subject == stored["id"]


def test_only_a_hash_of_the_secret_is_stored(repo: Repo) -> None:
    stored, token = _issue(repo)
    secret = token.split("_", 2)[2]
    row = repo.key_get(str(stored["id"]))
    assert row is not None
    assert secret not in str(row) and row["secret_hash"] != secret
    assert "secret_hash" not in stored  # never returned to the caller


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),  # wrong secret
        lambda t: t + "x",
        lambda t: PREFIX + "0" * 12 + "_" + "s" * 40,  # unknown id
        lambda t: PREFIX + "short",  # malformed
        lambda t: PREFIX + t[len(PREFIX) : len(PREFIX) + 12] + "-" + "s",  # bad separator
    ],
)
def test_wrong_keys_are_refused(repo: Repo, mangle: object) -> None:
    _, token = _issue(repo)
    with pytest.raises(AuthError):
        resolve_key(repo, mangle(token))  # type: ignore[operator]


def test_a_revoked_key_is_refused(repo: Repo) -> None:
    stored, token = _issue(repo)
    assert repo.key_revoke(str(stored["id"])) is True
    with pytest.raises(AuthError, match="revoked"):
        resolve_key(repo, token)
    assert repo.key_revoke(str(stored["id"])) is False  # already revoked


def test_an_expired_key_is_refused(repo: Repo) -> None:
    stored, token = _issue(repo, expires_days=1)
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    repo._exec("UPDATE api_keys SET expires_at = ? WHERE id = ?", (past, stored["id"]))
    with pytest.raises(AuthError, match="expired"):
        resolve_key(repo, token)


def test_a_future_expiry_is_fine(repo: Repo) -> None:
    _, token = _issue(repo, expires_days=30)
    assert resolve_key(repo, token).tenant == "acme"


def test_revoking_is_scoped_to_the_tenant(repo: Repo) -> None:
    stored, token = _issue(repo)
    assert repo.key_revoke(str(stored["id"]), tenant="globex") is False
    assert resolve_key(repo, token).tenant == "acme"
    assert repo.key_revoke(str(stored["id"]), tenant="acme") is True


def test_listing_is_scoped_to_the_tenant(repo: Repo) -> None:
    _issue(repo, tenant="acme", name="a")
    _issue(repo, tenant="globex", name="g")
    assert [k["name"] for k in repo.key_list("acme")] == ["a"]
    assert len(repo.key_list()) == 2


def test_last_used_is_recorded(repo: Repo) -> None:
    stored, token = _issue(repo)
    assert repo.key_get(str(stored["id"]))["last_used_at"] is None  # type: ignore[index]
    resolve_key(repo, token)
    assert repo.key_get(str(stored["id"]))["last_used_at"]  # type: ignore[index]


@pytest.mark.parametrize("roles", [[], ["root"], ["viewer", "root"]])
def test_invalid_roles_are_refused(repo: Repo, roles: list[str]) -> None:
    with pytest.raises(ValueError):
        _issue(repo, roles=roles)


def test_keys_are_unique(repo: Repo) -> None:
    tokens = {_issue(repo, name=f"k{i}")[1] for i in range(5)}
    assert len(tokens) == 5
