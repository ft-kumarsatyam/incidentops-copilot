"""The authenticated caller and the role model.

Roles are cumulative: each one can do everything the roles before it can.

    viewer     read incidents, their audit trail and the live stream
    responder  + open incidents, retry a failed analysis or dispatch, see the queue
    approver   + approve or reject proposed actions (the AI and responders only *propose*)
    admin      + manage API keys, search/export/verify the audit log, see decision-model usage
"""

from dataclasses import dataclass

ROLES = ("viewer", "responder", "approver", "admin")

PERMISSIONS: dict[str, str] = {
    "incident:read": "viewer",
    "incident:create": "responder",
    "incident:retry": "responder",
    "queue:read": "responder",
    "incident:decide": "approver",
    "decision:invoke": "responder",  # ask the Jev decision model a question (POST /decisions)
    "decision:admin": "admin",  # its configuration and usage (GET /decisions/status)
    "audit:search": "admin",
    "keys:manage": "admin",
}


@dataclass(frozen=True)
class Principal:
    subject: str  # stable identifier: the IdP's `sub`, or the API key id
    actor: str  # the label written to the audit log
    tenant: str
    roles: frozenset[str]
    method: str  # oidc | api-key | shared-key | anonymous

    @property
    def level(self) -> int:
        """Highest role held, as an index into ROLES (-1 for none)."""
        return max((ROLES.index(role) for role in self.roles if role in ROLES), default=-1)

    @property
    def named(self) -> bool:
        """True when the identity was verified and is unique to one person or service."""
        return self.method in ("oidc", "api-key")

    def can(self, permission: str) -> bool:
        return self.level >= ROLES.index(PERMISSIONS[permission])

    def describe(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "actor": self.actor,
            "tenant": self.tenant,
            "roles": sorted(self.roles, key=ROLES.index),
            "method": self.method,
            "permissions": sorted(name for name in PERMISSIONS if self.can(name)),
        }
