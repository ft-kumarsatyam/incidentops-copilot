"""Validate OIDC access tokens (JWTs) from an identity provider such as Okta, Entra ID,
Auth0 or Keycloak.

Checks the signature against the provider's JWKS, the issuer, the audience and the expiry, and
only accepts asymmetric algorithms (never ``none`` or HMAC, which would let anyone forge a token
using the public key). Roles and tenant come from configurable claims.
"""

import re
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from incidentops.core.config import Settings
from incidentops.security.auth import AuthError
from incidentops.security.principal import ROLES, Principal

_TENANT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_FORBIDDEN_ALGORITHMS = {"none", "HS256", "HS384", "HS512"}
_JWKS_TTL = 3600.0
_REFETCH_MIN_INTERVAL = 30.0  # an unknown `kid` triggers at most one refetch per this long

KeyResolver = Callable[[str | None], Any]


def _claim(claims: dict[str, Any], path: str) -> Any:
    """Read a claim by dotted path (``realm_access.roles``). A literal key wins over a path."""
    if path in claims:
        return claims[path]
    node: Any = claims
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class OidcVerifier:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        key_resolver: KeyResolver | None = None,
    ) -> None:
        try:
            import jwt
        except ImportError as error:  # pragma: no cover - only without the extra
            raise RuntimeError(
                "OIDC_ISSUER needs the OIDC extra: pip install 'incidentops[oidc]'"
            ) from error
        self._jwt = jwt
        self._settings = settings
        self._http = http or httpx.Client(timeout=5.0)
        self._owns_http = http is None
        self._resolver = key_resolver
        self._lock = threading.Lock()
        self._jwks: dict[str, Any] = {}
        self._fetched_at = 0.0
        bad = set(settings.oidc_algorithms) & _FORBIDDEN_ALGORITHMS
        if bad:
            raise ValueError(f"OIDC_ALGORITHMS may not include {', '.join(sorted(bad))}")

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # -- keys ------------------------------------------------------------------------------
    def _jwks_url(self) -> str:
        if self._settings.oidc_jwks_url:
            return self._settings.oidc_jwks_url
        issuer = (self._settings.oidc_issuer or "").rstrip("/")
        response = self._http.get(f"{issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        return str(response.json()["jwks_uri"])

    def _refresh(self) -> None:
        response = self._http.get(self._jwks_url())
        response.raise_for_status()
        self._jwks = {k["kid"]: k for k in response.json().get("keys", []) if "kid" in k}
        self._fetched_at = time.monotonic()

    def _key_for(self, kid: str | None) -> Any:
        if self._resolver is not None:
            return self._resolver(kid)
        if not kid:
            raise AuthError("token has no key id")
        with self._lock:
            age = time.monotonic() - self._fetched_at
            if kid not in self._jwks and (age > _REFETCH_MIN_INTERVAL or not self._jwks):
                self._refresh()
            elif age > _JWKS_TTL:
                self._refresh()
            jwk = self._jwks.get(kid)
        if jwk is None:
            raise AuthError("signing key not found")
        return self._jwt.PyJWK.from_dict(jwk).key

    # -- verification ----------------------------------------------------------------------
    def verify(self, token: str) -> Principal:
        settings = self._settings
        try:
            header = self._jwt.get_unverified_header(token)
            alg = header.get("alg")
            if alg not in settings.oidc_algorithms:
                raise AuthError(f"algorithm {alg!r} is not allowed")
            claims: dict[str, Any] = self._jwt.decode(
                token,
                self._key_for(header.get("kid")),
                algorithms=[alg],
                audience=settings.oidc_audience,
                issuer=settings.oidc_issuer,
                leeway=settings.oidc_leeway_seconds,
                options={"require": ["exp", "iss", "sub"]},
            )
        except AuthError:
            raise
        except (self._jwt.PyJWTError, httpx.HTTPError, KeyError, ValueError) as error:
            raise AuthError(f"token rejected: {type(error).__name__}") from error
        return self._principal(claims)

    def _principal(self, claims: dict[str, Any]) -> Principal:
        settings = self._settings
        raw_roles = _claim(claims, settings.oidc_roles_claim)
        if isinstance(raw_roles, str):
            raw_roles = raw_roles.replace(",", " ").split()
        roles = frozenset(r for r in (raw_roles or []) if isinstance(r, str) and r in ROLES)
        tenant = _claim(claims, settings.oidc_tenant_claim) or settings.default_tenant
        if not isinstance(tenant, str) or not _TENANT.match(tenant):
            raise AuthError("token carries an invalid tenant")
        subject = str(claims["sub"])
        actor = str(_claim(claims, settings.oidc_actor_claim) or subject)[:128]
        return Principal(subject, actor, tenant, roles, "oidc")
