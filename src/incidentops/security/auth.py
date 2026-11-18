"""Turn the credentials on a request into a Principal."""

import hmac
from typing import Protocol

from incidentops.core.config import Settings
from incidentops.persistence.base import Repo
from incidentops.security.principal import ROLES, Principal


class AuthError(Exception):
    """Authentication failed. ``reason`` is for the audit log; the caller only sees a 401."""

    def __init__(self, reason: str, **detail: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _clean_actor(value: str | None) -> str:
    cleaned = "".join(ch for ch in (value or "") if ch.isprintable())[:64].strip()
    return cleaned or "api"


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class Authenticator:
    """Accepts, in order: a scoped key or OIDC token in ``Authorization: Bearer``, a scoped key
    in ``X-API-Key``, then the legacy shared ``INCIDENTOPS_API_KEY`` in ``X-API-Key``.

    With no shared key and no OIDC configured (dev) a request without credentials is allowed as
    an anonymous admin, as before. A credential that is presented but wrong is always refused.
    """

    def __init__(self, settings: Settings, repo: Repo, oidc: TokenVerifier | None = None) -> None:
        self._settings = settings
        self._repo = repo
        self._oidc = oidc

    @property
    def open_access(self) -> bool:
        return self._settings.incidentops_api_key is None and self._oidc is None

    def authenticate(
        self,
        *,
        authorization: str | None = None,
        api_key: str | None = None,
        actor_header: str | None = None,
    ) -> Principal:
        from incidentops.security import apikeys  # local: apikeys imports AuthError from here

        bearer = None
        if authorization:
            scheme, _, value = authorization.partition(" ")
            if scheme.lower() != "bearer" or not value.strip():
                raise AuthError("unsupported Authorization header")
            bearer = value.strip()

        token = bearer or api_key
        if token and token.startswith(apikeys.PREFIX):
            return apikeys.resolve_key(self._repo, token)
        if bearer:
            if self._oidc is None:
                raise AuthError("bearer token presented but OIDC is not configured")
            return self._oidc.verify(bearer)

        shared = self._settings.incidentops_api_key
        if api_key:
            if shared is None or not hmac.compare_digest(api_key, shared):
                raise AuthError("invalid API key")
            return Principal(
                "shared-key",
                _clean_actor(actor_header),
                self._settings.default_tenant,
                frozenset(ROLES),
                "shared-key",
            )
        if self.open_access:
            return Principal(
                "anonymous",
                _clean_actor(actor_header),
                self._settings.default_tenant,
                frozenset(ROLES),
                "anonymous",
            )
        raise AuthError("no credentials")
