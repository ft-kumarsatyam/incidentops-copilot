"""Storage for custom roles (the ``roles`` table), shared by both repositories.

A role belongs to one tenant and is a named set of permissions, optionally limited to the
services that match its glob patterns. The four built-in roles are not stored: they live in
``security/principal.py``.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _split(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


class RoleQueries:
    """Role operations over ``?`` placeholders. Subclasses supply ``_rows`` and ``_exec``."""

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        raise NotImplementedError

    def role_upsert(
        self,
        tenant: str,
        name: str,
        *,
        description: str,
        permissions: list[str],
        services: list[str],
        created_by: str,
    ) -> dict[str, Any]:
        """Create the role, or replace its description, permissions and services."""
        now = _now()
        self._exec(
            "INSERT INTO roles (tenant_id, name, description, permissions, services, "
            "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, name) DO UPDATE SET description=excluded.description, "
            "permissions=excluded.permissions, services=excluded.services, "
            "updated_at=excluded.updated_at",
            (
                tenant,
                name,
                description,
                ",".join(permissions),
                ",".join(services),
                created_by,
                now,
                now,
            ),
        )
        stored = self.role_get_many(tenant, [name])
        return stored[0]

    def role_get_many(self, tenant: str, names: list[str]) -> list[dict[str, Any]]:
        if not names:
            return []
        marks = ", ".join("?" for _ in names)
        rows = self._rows(
            f"SELECT * FROM roles WHERE tenant_id = ? AND name IN ({marks}) ORDER BY name",
            (tenant, *names),
        )
        return [self._role(row) for row in rows]

    def role_list(self, tenant: str) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM roles WHERE tenant_id = ? ORDER BY name", (tenant,))
        return [self._role(row) for row in rows]

    def role_delete(self, tenant: str, name: str) -> bool:
        return self._exec("DELETE FROM roles WHERE tenant_id = ? AND name = ?", (tenant, name)) == 1

    @staticmethod
    def _role(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "name": row["name"],
            "tenant": row["tenant_id"],
            "description": row["description"],
            "permissions": _split(row["permissions"]),
            "services": _split(row["services"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
