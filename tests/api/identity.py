"""Shared helpers for the identity, tenancy and governance API tests."""

import copy
from typing import Any

from fastapi.testclient import TestClient

from incidentops.security.apikeys import issue_key
from tests.conftest import load_fixture

SHARED = {"X-API-Key": "shared-secret"}
PAYLOAD = load_fixture("incident_complete.json")
RUN = PAYLOAD["run_id"]


def payload(**changes: Any) -> dict[str, Any]:
    body = copy.deepcopy(PAYLOAD)
    body.update(changes)
    return body


def headers(
    client: TestClient, name: str, roles: list[str], tenant: str = "acme"
) -> dict[str, str]:
    """Mint a scoped key straight in the database and return the headers that use it."""
    _, token = issue_key(
        client.app.state.service.repo,  # type: ignore[attr-defined]
        name=name,
        tenant=tenant,
        roles=roles,
        created_by="test",
    )
    return {"Authorization": f"Bearer {token}"}


def decision(approved: bool = True, **extra: Any) -> dict[str, Any]:
    return {
        "idempotency_key": RUN,
        "decision": "approved" if approved else "rejected",
        **extra,
    }
