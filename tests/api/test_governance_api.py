"""Four-eyes approval, separation of duties and OIDC sign-in through the real API."""

import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from incidentops.persistence.audit import SYSTEM_TENANT
from incidentops.security.oidc import OidcVerifier
from tests.api.identity import PAYLOAD, RUN, SHARED, decision, headers

ALL_SEVERITIES = ["SEV-1", "SEV-2", "SEV-3", "SEV-4"]


def _client(settings: Settings, **updates: Any) -> TestClient:
    config = settings.model_copy(update={"incidentops_api_key": "shared-secret", **updates})
    return TestClient(create_app(config))


@pytest.fixture
def four_eyes(settings: Settings) -> Iterator[TestClient]:
    with _client(settings, four_eyes_approvals=2, four_eyes_severities=ALL_SEVERITIES) as c:
        yield c


def _incident(client: TestClient) -> dict[str, Any]:
    peek = headers(client, "peek", ["viewer"])  # same tenant as the incident (acme)
    return client.get(f"/incidents/{RUN}", headers=peek).json()  # type: ignore[no-any-return]


def _decide(client: TestClient, hdr: dict[str, str], **extra: Any) -> Any:
    return client.post(f"/incidents/{RUN}/decision", json=decision(**extra), headers=hdr)


def _setup(client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    client.post("/incidents", json=PAYLOAD, headers=headers(client, "opener", ["responder"]))
    return headers(client, "alice", ["approver"]), headers(client, "bob", ["approver"])


# -- four-eyes -----------------------------------------------------------------------------
def test_one_approval_is_recorded_but_does_not_act(four_eyes: TestClient) -> None:
    alice, _ = _setup(four_eyes)
    reader = headers(four_eyes, "reader", ["viewer"])

    first = _decide(four_eyes, alice)

    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "awaiting_approval"
    assert body["approval_progress"]["required"] == 2
    assert body["approval_progress"]["approved_by"] == ["key:alice"]  # a name, not a key id
    # visible to anyone who opens the incident, so the second approver knows they are needed
    assert four_eyes.get(f"/incidents/{RUN}", headers=reader).json()["approval_progress"]


def test_a_second_different_approver_completes_it(four_eyes: TestClient) -> None:
    alice, bob = _setup(four_eyes)
    _decide(four_eyes, alice)

    second = _decide(four_eyes, bob)

    assert second.status_code == 200 and second.json()["status"] == "succeeded"
    events = four_eyes.get(
        f"/audit?run_id={RUN}", headers=headers(four_eyes, "root", ["admin"])
    ).json()
    names = [e["event"] for e in events]
    assert names.count("approval:recorded") == 2  # one per approver (the 2nd led to the decision)
    decided = next(e for e in events if e["event"] == "decision:approved")
    assert decided["actor"] == "key:bob" and len(decided["detail"]["approvers"]) == 2


def test_the_same_approver_twice_does_not_count_double(four_eyes: TestClient) -> None:
    alice, _ = _setup(four_eyes)
    _decide(four_eyes, alice)

    again = _decide(four_eyes, alice)

    assert again.status_code == 200 and again.json()["status"] == "awaiting_approval"
    assert len(again.json()["approval_progress"]["approved_by"]) == 1


def test_the_shared_key_cannot_provide_either_approval(four_eyes: TestClient) -> None:
    four_eyes.post("/incidents", json=PAYLOAD, headers=SHARED)  # default tenant, shared key
    refused = _decide(four_eyes, SHARED | {"X-Actor": "alice"})
    assert refused.status_code == 403 and "named identity" in refused.json()["detail"]
    assert (
        four_eyes.get(f"/incidents/{RUN}", headers=SHARED).json()["status"] == "awaiting_approval"
    )


def test_a_single_rejection_is_enough(four_eyes: TestClient) -> None:
    alice, bob = _setup(four_eyes)
    _decide(four_eyes, alice)

    rejected = four_eyes.post(f"/incidents/{RUN}/decision", json=decision(False), headers=bob)

    assert rejected.status_code == 200 and rejected.json()["status"] == "cancelled"


def test_approvers_must_agree_on_the_actions(four_eyes: TestClient) -> None:
    alice, bob = _setup(four_eyes)
    proposed = _incident(four_eyes)["proposed_actions"]
    assert len(proposed) >= 2

    _decide(four_eyes, alice, approved_actions=proposed[:1])
    mismatch = _decide(four_eyes, bob, approved_actions=proposed[1:])

    assert mismatch.status_code == 409 and "same actions" in mismatch.json()["detail"]
    agreed = _decide(four_eyes, bob, approved_actions=proposed[:1])
    assert agreed.json()["status"] == "succeeded"


def test_only_the_configured_severities_need_two_people(settings: Settings) -> None:
    # the fixture incident is SEV-1, so a policy for SEV-2 alone must not apply to it
    with _client(settings, four_eyes_approvals=2, four_eyes_severities=["SEV-2"]) as client:
        alice, _ = _setup(client)
        assert _incident(client)["severity"] == "SEV-1"
        assert _decide(client, alice).json()["status"] == "succeeded"


def test_the_default_needs_only_one_approver(settings: Settings) -> None:
    with _client(settings) as client:
        alice, _ = _setup(client)
        assert _decide(client, alice).json()["status"] == "succeeded"
        assert "approval_progress" not in _incident(client)


def test_a_race_between_the_two_approvers_decides_exactly_once(four_eyes: TestClient) -> None:
    from concurrent.futures import ThreadPoolExecutor

    alice, bob = _setup(four_eyes)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda h: _decide(four_eyes, h).status_code, [alice, bob]))

    assert sorted(results)[0] == 200
    assert set(results) <= {200, 409}
    final = _incident(four_eyes)
    assert final["status"] in {"succeeded", "awaiting_approval"}
    events = four_eyes.get(
        f"/audit?run_id={RUN}&event=decision", headers=headers(four_eyes, "root", ["admin"])
    ).json()
    assert len(events) <= 1  # never decided twice


# -- separation of duties ------------------------------------------------------------------
def test_the_person_who_opened_an_incident_cannot_approve_it(settings: Settings) -> None:
    with _client(settings, forbid_self_approval=True) as client:
        both = headers(client, "alice", ["responder", "approver"])
        other = headers(client, "bob", ["approver"])
        client.post("/incidents", json=PAYLOAD, headers=both)

        refused = _decide(client, both)

        assert refused.status_code == 403 and "cannot also approve" in refused.json()["detail"]
        assert _incident(client)["status"] == "awaiting_approval"
        assert _decide(client, other).json()["status"] == "succeeded"


def test_self_approval_is_allowed_unless_forbidden(settings: Settings) -> None:
    with _client(settings) as client:
        both = headers(client, "alice", ["responder", "approver"])
        client.post("/incidents", json=PAYLOAD, headers=both)
        assert _decide(client, both).json()["status"] == "succeeded"


# -- OIDC sign-in --------------------------------------------------------------------------
ISSUER, AUDIENCE = "https://idp.example.com", "incidentops"


@pytest.fixture(scope="module")
def idp_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def sso(
    settings: Settings, idp_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    public = idp_key.public_key()
    monkeypatch.setattr(
        "incidentops.api.app.OidcVerifier",
        lambda s: OidcVerifier(s, key_resolver=lambda kid: public),
    )
    config = settings.model_copy(
        update={"incidentops_env": "prod", "oidc_issuer": ISSUER, "oidc_audience": AUDIENCE}
    )
    with TestClient(create_app(config)) as client:
        yield client


def _bearer(key: rsa.RSAPrivateKey, **claims: Any) -> dict[str, str]:
    now = int(time.time())
    body = {"iss": ISSUER, "aud": AUDIENCE, "sub": "u-1", "exp": now + 300, **claims}
    return {
        "Authorization": "Bearer " + jwt.encode(body, key, algorithm="RS256", headers={"kid": "k"})
    }


def test_a_signed_in_user_acts_under_their_own_identity(
    sso: TestClient, idp_key: rsa.RSAPrivateKey
) -> None:
    responder = _bearer(idp_key, sub="ana", roles=["responder"], tenant_id="acme")
    approver = _bearer(idp_key, sub="raj", roles=["approver"], tenant_id="acme")

    assert sso.post("/incidents", json=PAYLOAD, headers=responder).status_code == 200
    assert (
        sso.post(f"/incidents/{RUN}/decision", json=decision(), headers=responder).status_code
        == 403
    )
    assert (
        sso.post(f"/incidents/{RUN}/decision", json=decision(), headers=approver).status_code == 200
    )

    audit = sso.get(f"/incidents/{RUN}/audit", headers=approver).json()
    assert [e["actor"] for e in audit] == ["ana", "raj", "raj"]
    assert sso.get("/me", headers=approver).json()["method"] == "oidc"


def test_oidc_tenants_are_isolated(sso: TestClient, idp_key: rsa.RSAPrivateKey) -> None:
    acme = _bearer(idp_key, roles=["responder"], tenant_id="acme")
    sso.post("/incidents", json=PAYLOAD, headers=acme)

    other = _bearer(idp_key, roles=["admin"], tenant_id="globex")
    assert sso.get(f"/incidents/{RUN}", headers=other).status_code == 404


@pytest.mark.parametrize(
    "claims", [{"exp": 1}, {"aud": "other"}, {"iss": "https://evil.example.com"}]
)
def test_bad_tokens_are_refused_and_audited(
    sso: TestClient, idp_key: rsa.RSAPrivateKey, claims: dict[str, Any]
) -> None:
    assert (
        sso.get("/incidents", headers=_bearer(idp_key, roles=["admin"], **claims)).status_code
        == 401
    )
    rows, _ = sso.app.state.service.repo.audit_search(tenant=SYSTEM_TENANT)  # type: ignore[attr-defined]
    assert rows[-1]["event"] == "auth:denied" and "token rejected" in rows[-1]["detail"]["reason"]


def test_a_token_from_an_unknown_signer_is_refused(sso: TestClient) -> None:
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert sso.get("/incidents", headers=_bearer(stranger, roles=["admin"])).status_code == 401


def test_scoped_keys_work_alongside_oidc(sso: TestClient) -> None:
    hdr = headers(sso, "ci", ["viewer"])
    assert sso.get("/incidents", headers=hdr).status_code == 200


def test_there_is_no_open_access_once_oidc_is_on(sso: TestClient) -> None:
    assert sso.get("/incidents").status_code == 401
