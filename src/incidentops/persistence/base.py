"""The storage interface the service depends on. SQLite and Postgres both implement it."""

from typing import Any, Protocol

from incidentops.persistence.audit import ChainReport


class Repo(Protocol):
    def ping(self) -> bool: ...

    def upsert(self, state: dict[str, Any], tenant: str = "default") -> None: ...

    def get(self, run_id: str, *, tenant: str | None = None) -> dict[str, Any] | None: ...

    def tenant_of(self, run_id: str) -> str | None: ...

    def search(
        self,
        *,
        statuses: list[str] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        tenant: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def insert_new(self, state: dict[str, Any], tenant: str = "default") -> bool: ...

    def claim(
        self, run_id: str, expected: str, to: str = "busy", *, visible: bool = False
    ) -> bool: ...

    def release(self, run_id: str, status: str) -> None: ...

    def add_audit(
        self,
        run_id: str,
        event: str,
        actor: str,
        detail: dict[str, Any] | None = None,
        tenant: str | None = None,
    ) -> None: ...

    def audit_for(self, run_id: str, *, tenant: str | None = None) -> list[dict[str, Any]]: ...

    def audit_search(
        self,
        *,
        tenant: str | None,
        run_id: str | None = None,
        event: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def audit_tenants(self) -> list[str]: ...

    def audit_verify(self, tenant: str) -> ChainReport: ...

    def audit_prune(self, before: str, *, tenant: str | None = None) -> int: ...

    def add_approval(
        self, run_id: str, approver: str, actions: list[str], actor: str = ""
    ) -> list[str]: ...

    def approvals_for(self, run_id: str) -> list[dict[str, Any]]: ...

    def key_insert(
        self,
        key_id: str,
        name: str,
        secret_hash: str,
        tenant: str,
        roles: list[str],
        created_by: str,
        expires_at: str | None,
    ) -> None: ...

    def key_get(self, key_id: str) -> dict[str, Any] | None: ...

    def key_list(self, tenant: str | None = None) -> list[dict[str, Any]]: ...

    def key_revoke(self, key_id: str, tenant: str | None = None) -> bool: ...

    def key_touch(self, key_id: str) -> None: ...

    def role_upsert(
        self,
        tenant: str,
        name: str,
        *,
        description: str,
        permissions: list[str],
        services: list[str],
        created_by: str,
    ) -> dict[str, Any]: ...

    def role_get_many(self, tenant: str, names: list[str]) -> list[dict[str, Any]]: ...

    def role_list(self, tenant: str) -> list[dict[str, Any]]: ...

    def role_delete(self, tenant: str, name: str) -> bool: ...

    def close(self) -> None: ...
