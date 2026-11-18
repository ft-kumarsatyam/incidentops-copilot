"""The storage interface the service depends on. SQLite and Postgres both implement it."""

from typing import Any, Protocol


class Repo(Protocol):
    def ping(self) -> bool: ...

    def upsert(self, state: dict[str, Any]) -> None: ...

    def get(self, run_id: str) -> dict[str, Any] | None: ...

    def search(
        self,
        *,
        statuses: list[str] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def claim(self, run_id: str, expected: str) -> bool: ...

    def release(self, run_id: str, status: str) -> None: ...

    def add_audit(
        self, run_id: str, event: str, actor: str, detail: dict[str, Any] | None = None
    ) -> None: ...

    def audit_for(self, run_id: str) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...
