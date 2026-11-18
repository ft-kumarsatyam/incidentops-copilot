"""Storage for scoped API keys (the ``api_keys`` table), shared by both repositories.

Only a SHA-256 of the key's secret is stored, so a database leak does not reveal usable keys.
The secrets are 256-bit random values, which is why a plain hash is enough (there is nothing to
brute-force, unlike a password).
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class KeyQueries:
    """Key operations over ``?`` placeholders. Subclasses supply ``_rows`` and ``_exec``."""

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        raise NotImplementedError

    def key_insert(
        self,
        key_id: str,
        name: str,
        secret_hash: str,
        tenant: str,
        roles: list[str],
        created_by: str,
        expires_at: str | None,
    ) -> None:
        self._exec(
            "INSERT INTO api_keys (id, name, secret_hash, tenant_id, roles, created_by, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key_id, name, secret_hash, tenant, ",".join(roles), created_by, _now(), expires_at),
        )

    def key_get(self, key_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM api_keys WHERE id = ?", (key_id,))
        return self._key(rows[0]) if rows else None

    def key_list(self, tenant: str | None = None) -> list[dict[str, Any]]:
        if tenant is None:
            rows = self._rows("SELECT * FROM api_keys ORDER BY created_at")
        else:
            rows = self._rows(
                "SELECT * FROM api_keys WHERE tenant_id = ? ORDER BY created_at", (tenant,)
            )
        return [self._key(row) for row in rows]

    def key_revoke(self, key_id: str, tenant: str | None = None) -> bool:
        sql, params = (
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            [
                _now(),
                key_id,
            ],
        )
        if tenant is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant)
        return self._exec(sql, tuple(params)) == 1

    def key_touch(self, key_id: str) -> None:
        self._exec("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), key_id))

    @staticmethod
    def _key(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "secret_hash": row["secret_hash"],
            "tenant": row["tenant_id"],
            "roles": [role for role in row["roles"].split(",") if role],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "last_used_at": row["last_used_at"],
        }
