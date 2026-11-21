"""The authenticated caller and the role model.

Four built-in roles are cumulative: each one can do everything the roles before it can.

    viewer     read incidents, their audit trail and the live stream
    responder  + open incidents, retry a failed analysis or dispatch, see the queue
    approver   + approve or reject proposed actions (the AI and responders only *propose*)
    admin      + manage API keys and roles, search/export/verify the audit log, see
               decision-model usage

On top of those, a tenant can define **custom roles**: a named set of permissions, optionally
limited to the services matching some glob patterns (``checkout-*``). A caller holding a custom
role gets exactly what it lists, in addition to any built-in role they hold. The permission to
decide an incident is then checked against the incident's service as well (``can_on``).
"""

import fnmatch
from dataclasses import dataclass

ROLES = ("viewer", "responder", "approver", "admin")

PERMISSIONS: dict[str, str] = {
    "incident:read": "viewer",
    "incident:create": "responder",
    "incident:retry": "responder",
    "queue:read": "responder",
    "decision:invoke": "responder",  # ask the Jev decision model a question (POST /decisions)
    "incident:decide": "approver",
    # Approving the actions listed in HIGH_RISK_ACTIONS (rollback, scale ...) needs this on top.
    "incident:decide_high_risk": "approver",
    "audit:search": "admin",
    "keys:manage": "admin",
    "roles:manage": "admin",  # create, change and delete custom roles
    "decision:admin": "admin",  # its configuration and usage (GET /decisions/status)
}


@dataclass(frozen=True)
class Grant:
    """One permission a custom role gives, for all services or only those matching a pattern."""

    permission: str
    services: tuple[str, ...] = ()  # empty means every service
    role: str = ""

    def covers(self, service: str) -> bool:
        return not self.services or any(fnmatch.fnmatchcase(service, p) for p in self.services)


def _role_order(name: str) -> tuple[int, str]:
    """Built-in roles first in ascending order, then custom roles by name."""
    return (ROLES.index(name), "") if name in ROLES else (len(ROLES), name)


@dataclass(frozen=True)
class Principal:
    subject: str  # stable identifier: the IdP's `sub`, or the API key id
    actor: str  # the label written to the audit log
    tenant: str
    roles: frozenset[str]
    method: str  # oidc | api-key | shared-key | anonymous
    grants: tuple[Grant, ...] = ()  # from custom roles, resolved at authentication

    @property
    def level(self) -> int:
        """Highest built-in role held, as an index into ROLES (-1 for none)."""
        return max((ROLES.index(role) for role in self.roles if role in ROLES), default=-1)

    @property
    def named(self) -> bool:
        """True when the identity was verified and is unique to one person or service."""
        return self.method in ("oidc", "api-key")

    def can(self, permission: str) -> bool:
        """Whether the caller may do this at all (for any service). Routes check this."""
        if self.level >= ROLES.index(PERMISSIONS[permission]):
            return True
        return any(grant.permission == permission for grant in self.grants)

    def can_on(self, permission: str, service: str) -> bool:
        """Whether the caller may do this for an incident of ``service``. A built-in role that
        reaches the permission applies to every service; a custom role only to its own."""
        if self.level >= ROLES.index(PERMISSIONS[permission]):
            return True
        return any(g.permission == permission and g.covers(service) for g in self.grants)

    def describe(self) -> dict[str, object]:
        scoped = {
            f"{grant.role}:{grant.permission}": list(grant.services)
            for grant in self.grants
            if grant.services
        }
        return {
            "subject": self.subject,
            "actor": self.actor,
            "tenant": self.tenant,
            "roles": sorted(self.roles, key=_role_order),
            "method": self.method,
            "permissions": sorted(name for name in PERMISSIONS if self.can(name)),
            "scoped_permissions": scoped,
        }
