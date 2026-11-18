"""Opt-in webhook security: inbound HMAC on POST /incidents and the outbound host allowlist."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from incidentops.core.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, signature_headers
from tests.conftest import load_fixture

KEY = {"X-API-Key": "test-secret"}
SECRET = "inbound-secret"


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    secured = settings.model_copy(
        update={"incidentops_api_key": "test-secret", "inbound_signing_secret": SECRET}
    )
    with TestClient(create_app(secured)) as test_client:
        yield test_client


def _signed_post(client: TestClient, headers: dict[str, str] | None = None, **kw: Any) -> Any:
    body = json.dumps(load_fixture("incident_complete.json")).encode()
    signed = signature_headers(body, SECRET, **kw) if headers is None else headers
    return client.post(
        "/incidents",
        content=body,
        headers={**KEY, "Content-Type": "application/json", **signed},
    )


def test_a_correctly_signed_request_is_accepted(client: TestClient) -> None:
    assert _signed_post(client).status_code == 200


def test_an_unsigned_request_is_rejected_even_with_the_api_key(client: TestClient) -> None:
    payload = load_fixture("incident_complete.json")

    assert client.post("/incidents", json=payload, headers=KEY).status_code == 401


def test_a_wrong_signature_is_rejected(client: TestClient) -> None:
    headers = {TIMESTAMP_HEADER: "1", SIGNATURE_HEADER: "v1=deadbeef"}

    assert _signed_post(client, headers).status_code == 401


def test_a_stale_signed_request_is_rejected(client: TestClient) -> None:
    assert _signed_post(client, now=1_000).status_code == 401


def test_reads_do_not_need_a_signature(client: TestClient) -> None:
    assert client.get("/incidents", headers=KEY).status_code == 200


def test_signing_is_off_by_default(settings: Settings) -> None:
    open_settings = settings.model_copy(update={"incidentops_api_key": "test-secret"})
    with TestClient(create_app(open_settings)) as plain:
        payload = load_fixture("incident_complete.json")

        assert plain.post("/incidents", json=payload, headers=KEY).status_code == 200
