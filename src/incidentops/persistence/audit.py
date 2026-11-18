"""Tamper-evident audit log: a SHA-256 hash chain per tenant, plus search, verify and retention.

Every entry stores ``prev_hash`` (the hash of the entry before it in the same tenant's chain) and
its own ``hash`` over the previous hash and the entry's fields. Editing, deleting or reordering a
stored entry breaks every hash after it. Dropping the newest entries is caught because the chain
head (``audit_heads``) records the latest sequence number and hash. Entries are serialised per
tenant (a row lock on the head), so concurrent writers on several replicas cannot fork the chain.

The chain proves the log was not changed *through the database's ordinary tables*. It does not
stop someone with full database control from rewriting the log and the head together. For that,
copy ``incidentops audit export`` to storage the database operator cannot write (WORM bucket,
SIEM) and compare heads. See docs/SECURITY.md.

Entries written before this feature existed have ``seq = 0`` and are not part of any chain.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

SYSTEM_TENANT = "_system"  # authentication failures and other events with no tenant
DEFAULT_TENANT = "default"


def canonical(
    tenant: str, seq: int, run_id: str, event: str, actor: str, detail_json: str, at: str
) -> str:
    """The exact text that is hashed. JSON with fixed separators so it never changes shape."""
    return json.dumps(
        [tenant, seq, run_id, event, actor, detail_json, at],
        separators=(",", ":"),
        ensure_ascii=True,
    )


def entry_hash(prev_hash: str, text: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{text}".encode()).hexdigest()


@dataclass(frozen=True)
class ChainReport:
    tenant: str
    ok: bool
    checked: int
    head_seq: int
    error: str | None = None
    bad_seq: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_chain(
    tenant: str,
    rows: Iterable[Mapping[str, Any]],
    head: Mapping[str, Any] | None,
    *,
    anchor_seq: int = 0,
    anchor_hash: str = "",
) -> ChainReport:
    """Recompute the chain over ``rows`` (ordered by seq, all with seq > anchor_seq)."""
    expected_seq, prev, checked = anchor_seq + 1, anchor_hash, 0
    for row in rows:
        seq = int(row["seq"])
        if seq != expected_seq:
            kind = "missing" if seq > expected_seq else "duplicate or out-of-order"
            return ChainReport(
                tenant, False, checked, 0, f"{kind} entry before seq {seq}", expected_seq
            )
        if row["prev_hash"] != prev:
            return ChainReport(tenant, False, checked, 0, "prev_hash does not match", seq)
        text = canonical(
            tenant, seq, row["run_id"], row["event"], row["actor"], row["detail_json"], row["at"]
        )
        if entry_hash(prev, text) != row["hash"]:
            return ChainReport(tenant, False, checked, 0, "entry was modified", seq)
        prev, expected_seq, checked = row["hash"], seq + 1, checked + 1
    last_seq = expected_seq - 1
    if head is None:
        if checked:
            return ChainReport(tenant, False, checked, 0, "chain head is missing", last_seq)
        return ChainReport(tenant, True, 0, 0)
    if int(head["seq"]) != last_seq or head["hash"] != prev:
        return ChainReport(
            tenant, False, checked, int(head["seq"]), "newest entries were removed", last_seq
        )
    return ChainReport(tenant, True, checked, last_seq)


_ENTRY_COLUMNS = "id, tenant_id, seq, run_id, event, actor, detail_json, at, hash"


def entry(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "seq": int(row["seq"]),
        "tenant": row["tenant_id"],
        "run_id": row["run_id"],
        "event": row["event"],
        "actor": row["actor"],
        "detail": json.loads(row["detail_json"]),
        "at": row["at"],
        "hash": row["hash"],
    }


class AuditQueries:
    """Read, verify and prune operations, written once over ``?`` placeholders.

    Subclasses provide ``_rows`` (run a query, return mapping rows) and ``_exec`` (run a
    statement, return the affected row count).
    """

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        raise NotImplementedError

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
    ) -> tuple[list[dict[str, Any]], int]:
        clauses, params = ["seq > 0"], []
        if tenant is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if event:
            clauses.append("(event = ? OR event LIKE ?)")  # 'decision' matches decision:approved
            params.extend([event, f"{event}:%"])
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if since:
            clauses.append("at >= ?")
            params.append(since)
        if until:
            clauses.append("at < ?")
            params.append(until)
        where = " AND ".join(clauses)
        total = self._rows(f"SELECT COUNT(*) AS n FROM audit_log WHERE {where}", tuple(params))
        rows = self._rows(
            f"SELECT {_ENTRY_COLUMNS} FROM audit_log WHERE {where} "
            "ORDER BY tenant_id, seq LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [entry(row) for row in rows], int(total[0]["n"])

    def audit_tenants(self) -> list[str]:
        return [row["tenant_id"] for row in self._rows("SELECT tenant_id FROM audit_heads")]

    def audit_verify(self, tenant: str) -> ChainReport:
        """Check one tenant's chain. A write landing between the two reads can look like a
        missing tail, so that one verdict is re-read before it is believed."""
        report = self._verify_once(tenant)
        for _ in range(3):
            if report.ok or report.error != "newest entries were removed":
                break
            report = self._verify_once(tenant)
        return report

    def _verify_once(self, tenant: str) -> ChainReport:
        heads = self._rows(
            "SELECT seq, hash, anchor_seq, anchor_hash FROM audit_heads WHERE tenant_id = ?",
            (tenant,),
        )
        head = heads[0] if heads else None
        anchor_seq = int(head["anchor_seq"]) if head else 0
        anchor_hash = head["anchor_hash"] if head else ""
        rows = self._rows(
            "SELECT seq, run_id, event, actor, detail_json, at, prev_hash, hash FROM audit_log "
            "WHERE tenant_id = ? AND seq > ? ORDER BY seq",
            (tenant, anchor_seq),
        )
        return verify_chain(tenant, rows, head, anchor_seq=anchor_seq, anchor_hash=anchor_hash)

    def audit_prune(self, before: str, *, tenant: str | None = None) -> int:
        """Delete chained entries older than ``before`` (an ISO timestamp), oldest first.

        The newest deleted entry becomes the chain's anchor, so verification continues to work
        over what remains. Only a contiguous prefix is removed. Returns the number deleted.
        """
        removed = 0
        for name in [tenant] if tenant else self.audit_tenants():
            edge = self._rows(
                "SELECT seq, hash FROM audit_log WHERE tenant_id = ? AND seq > 0 AND at < ? "
                "ORDER BY seq DESC LIMIT 1",
                (name, before),
            )
            if not edge:
                continue
            seq, digest = int(edge[0]["seq"]), edge[0]["hash"]
            # Keep the anchor and the deletion together so a crash cannot strand a hole.
            self._exec(
                "UPDATE audit_heads SET anchor_seq = ?, anchor_hash = ? "
                "WHERE tenant_id = ? AND anchor_seq < ?",
                (seq, digest, name, seq),
            )
            removed += self._exec(
                "DELETE FROM audit_log WHERE tenant_id = ? AND seq > 0 AND seq <= ?", (name, seq)
            )
        return removed


class ApprovalQueries:
    """Four-eyes approvals, written once. Subclasses supply ``_rows`` and ``_exec``."""

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        raise NotImplementedError

    def add_approval(
        self, run_id: str, approver: str, actions: list[str], actor: str = ""
    ) -> list[str]:
        """Record this approver's approval (once each). Returns everyone who has approved."""
        self._exec(
            "INSERT INTO approvals (run_id, approver, actor, actions_json, at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (run_id, approver) DO NOTHING",
            (
                run_id,
                approver,
                actor,
                json.dumps(sorted(actions)),
                datetime.now(UTC).isoformat(timespec="milliseconds"),
            ),
        )
        return [row["approver"] for row in self.approvals_for(run_id)]

    def approvals_for(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT approver, actor, actions_json, at FROM approvals WHERE run_id = ? ORDER BY at",
            (run_id,),
        )
        return [
            {
                "approver": r["approver"],
                "actor": r["actor"] or r["approver"],
                "actions": json.loads(r["actions_json"]),
                "at": r["at"],
            }
            for r in rows
        ]
