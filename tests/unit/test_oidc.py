"""OIDC token validation: signature, issuer, audience, expiry, algorithms, claims, key rotation."""

import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from incidentops.core.config import Settings
from incidentops.security import AuthError
from incidentops.security.oidc import OidcVerifier

ISSUER = "https://idp.example.com"
AUDIENCE = "incidentops"


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return _key()


def _jwk(private: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    jwk: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return {**jwk, "kid": kid, "alg": "RS256", "use": "sig"}


def _token(private: Any, kid: str | None = "k1", alg: str = "RS256", **claims: Any) -> str:
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "exp": now + 300,
        "iat": now,
        "roles": ["approver"],
        **claims,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, private, algorithm=alg, headers={"kid": kid} if kid else {})


def _settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        incidentops_env="dev",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        **overrides,
    )


def _verifier(signing_key: rsa.RSAPrivateKey, **overrides: Any) -> OidcVerifier:
    public = signing_key.public_key()
    return OidcVerifier(_settings(**overrides), key_resolver=lambda kid: public)


def test_a_valid_token_becomes_a_named_principal(signing_key: rsa.RSAPrivateKey) -> None:
    principal = _verifier(signing_key).verify(_token(signing_key, tenant_id="acme"))
    assert principal.method == "oidc" and principal.named
    assert principal.subject == "user-1" and principal.actor == "user-1"
    assert principal.tenant == "acme" and principal.roles == {"approver"}


def test_the_tenant_falls_back_to_the_default(signing_key: rsa.RSAPrivateKey) -> None:
    principal = _verifier(signing_key).verify(_token(signing_key))
    assert principal.tenant == "default"


@pytest.mark.parametrize("tenant", ["a b", "x/../y", "t" * 65, 123, ["a"]])
def test_an_invalid_tenant_claim_is_refused(signing_key: rsa.RSAPrivateKey, tenant: Any) -> None:
    with pytest.raises(AuthError, match="tenant"):
        _verifier(signing_key).verify(_token(signing_key, tenant_id=tenant))


def test_the_actor_comes_from_the_configured_claim(signing_key: rsa.RSAPrivateKey) -> None:
    verifier = _verifier(signing_key, oidc_actor_claim="email")
    principal = verifier.verify(_token(signing_key, email="ana@example.com"))
    assert principal.actor == "ana@example.com" and principal.subject == "user-1"


@pytest.mark.parametrize(
    ("claim", "value", "expected"),
    [
        ("roles", ["viewer", "responder"], {"viewer", "responder"}),
        ("roles", "viewer approver", {"viewer", "approver"}),
        ("roles", "viewer,admin", {"viewer", "admin"}),
        (
            "roles",
            ["root", "viewer", 7, "bad name!"],
            {"root", "viewer"},
        ),  # names are kept (they only matter if the tenant defines them); non-names are dropped
        ("roles", None, set()),
    ],
)
def test_roles_come_from_the_claim(
    signing_key: rsa.RSAPrivateKey, claim: str, value: Any, expected: set[str]
) -> None:
    principal = _verifier(signing_key).verify(_token(signing_key, **{claim: value}))
    assert principal.roles == expected


def test_roles_can_be_read_from_a_nested_claim(signing_key: rsa.RSAPrivateKey) -> None:
    verifier = _verifier(signing_key, oidc_roles_claim="realm_access.roles")
    token = _token(signing_key, roles=None, realm_access={"roles": ["admin"]})
    assert verifier.verify(token).roles == {"admin"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 3600},  # expired
        {"iss": "https://evil.example.com"},
        {"aud": "another-app"},
        {"aud": None},
        {"sub": None},
        {"exp": None},
    ],
)
def test_bad_claims_are_refused(signing_key: rsa.RSAPrivateKey, overrides: dict[str, Any]) -> None:
    with pytest.raises(AuthError):
        _verifier(signing_key).verify(_token(signing_key, **overrides))


def test_a_token_signed_by_another_key_is_refused(signing_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(AuthError):
        _verifier(signing_key).verify(_token(_key()))


def test_clock_leeway_is_applied(signing_key: rsa.RSAPrivateKey) -> None:
    barely_expired = _token(signing_key, exp=int(time.time()) - 5)
    assert _verifier(signing_key, oidc_leeway_seconds=30).verify(barely_expired)
    with pytest.raises(AuthError):
        _verifier(signing_key, oidc_leeway_seconds=0).verify(barely_expired)


def test_an_unsigned_token_is_refused(signing_key: rsa.RSAPrivateKey) -> None:
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": 9999999999}, None, "none"
    )
    with pytest.raises(AuthError, match="not allowed"):
        _verifier(signing_key).verify(unsigned)


def test_an_hmac_token_forged_with_the_public_key_is_refused(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    """The classic algorithm-confusion attack: HS256 signed with the public key as the secret."""
    from cryptography.hazmat.primitives import serialization

    public_pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(AuthError, match="not allowed"):
        _verifier(signing_key).verify(_hs256_forgery(public_pem))


def _hs256_forgery(secret: bytes) -> str:
    import base64
    import hashlib
    import hmac

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    body = b64(
        json.dumps(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "attacker",
                "exp": 9999999999,
                "roles": ["admin"],
            }
        ).encode()
    )
    signature = hmac.new(secret, f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{b64(signature)}"


def test_hmac_algorithms_cannot_be_enabled() -> None:
    with pytest.raises(ValueError, match="HS256"):
        OidcVerifier(_settings(oidc_algorithms=["RS256", "HS256"]))


def test_garbage_is_refused(signing_key: rsa.RSAPrivateKey) -> None:
    for junk in ["", "abc", "a.b.c", "x" * 50]:
        with pytest.raises(AuthError):
            _verifier(signing_key).verify(junk)


# -- fetching keys from the provider ------------------------------------------------------
class _Provider:
    """A fake identity provider that counts how often its endpoints are hit."""

    def __init__(self, keys: dict[str, rsa.RSAPrivateKey]) -> None:
        self.keys = keys
        self.hits: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hits.append(request.url.path)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/keys"})
        if request.url.path == "/keys":
            return httpx.Response(
                200, json={"keys": [_jwk(k, kid) for kid, k in self.keys.items()]}
            )
        return httpx.Response(404)


def _remote(provider: _Provider, **overrides: Any) -> OidcVerifier:
    client = httpx.Client(transport=httpx.MockTransport(provider.handler))
    return OidcVerifier(_settings(**overrides), http=client)


def test_keys_are_discovered_from_the_issuer_and_cached(signing_key: rsa.RSAPrivateKey) -> None:
    provider = _Provider({"k1": signing_key})
    verifier = _remote(provider)

    assert verifier.verify(_token(signing_key)).subject == "user-1"
    assert verifier.verify(_token(signing_key)).subject == "user-1"

    assert provider.hits == ["/.well-known/openid-configuration", "/keys"]  # fetched once


def test_an_explicit_jwks_url_skips_discovery(signing_key: rsa.RSAPrivateKey) -> None:
    provider = _Provider({"k1": signing_key})
    verifier = _remote(provider, oidc_jwks_url=f"{ISSUER}/keys")
    verifier.verify(_token(signing_key))
    assert provider.hits == ["/keys"]


def test_a_rotated_key_is_picked_up(signing_key: rsa.RSAPrivateKey, monkeypatch: Any) -> None:
    provider = _Provider({"k1": signing_key})
    verifier = _remote(provider)
    verifier.verify(_token(signing_key))

    new_key = _key()
    provider.keys["k2"] = new_key
    monkeypatch.setattr("incidentops.security.oidc._REFETCH_MIN_INTERVAL", 0.0)

    assert verifier.verify(_token(new_key, kid="k2")).subject == "user-1"


def test_unknown_key_ids_cannot_force_constant_refetching(signing_key: rsa.RSAPrivateKey) -> None:
    provider = _Provider({"k1": signing_key})
    verifier = _remote(provider)
    verifier.verify(_token(signing_key))
    hits = len(provider.hits)

    for _ in range(5):
        with pytest.raises(AuthError):
            verifier.verify(_token(_key(), kid="unknown"))

    assert len(provider.hits) == hits  # inside the minimum interval: no refetch


def test_a_token_without_a_key_id_is_refused_when_keys_are_remote(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    verifier = _remote(_Provider({"k1": signing_key}))
    with pytest.raises(AuthError, match="key id"):
        verifier.verify(_token(signing_key, kid=None))


def test_an_unreachable_provider_is_a_refusal_not_a_crash(signing_key: rsa.RSAPrivateKey) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    verifier = OidcVerifier(_settings(), http=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(AuthError):
        verifier.verify(_token(signing_key))
