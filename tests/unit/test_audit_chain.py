"""The tamper-evident audit log: chaining, verification, search, retention. Both backends."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from incidentops.persistence.audit import SYSTEM_TENANT, canonical, entry_hash, verify_chain
from incidentops.persistence.base import Repo


def _fill(repo: Repo, count: int = 5, tenant: str = "acme") -> None:
    for i in range(count):
        repo.add_audit(f"r{i % 2}", f"event:{i}", f"actor{i % 3}", {"n": i}, tenant=tenant)


def _row(repo: Repo, tenant: str, seq: int) -> dict[str, Any]:
    rows, _ = repo.audit_search(tenant=tenant, limit=500)
    return next(r for r in rows if r["seq"] == seq)


# -- the pure chain ------------------------------------------------------------------------
def _chain(count: int) -> list[dict[str, Any]]:
    rows, prev = [], ""
    for seq in range(1, count + 1):
        row = {
            "seq": seq,
            "run_id": "r",
            "event": f"e{seq}",
            "actor": "a",
            "detail_json": "{}",
            "at": f"2026-01-01T00:00:0{seq}Z",
            "prev_hash": prev,
        }
        row["hash"] = entry_hash(prev, canonical("t", seq, "r", row["event"], "a", "{}", row["at"]))
        rows.append(row)
        prev = row["hash"]
    return rows


def test_a_valid_chain_verifies() -> None:
    rows = _chain(4)
    report = verify_chain("t", rows, {"seq": 4, "hash": rows[-1]["hash"]})
    assert report.ok and report.checked == 4 and report.head_seq == 4


def test_an_empty_chain_verifies() -> None:
    assert verify_chain("t", [], None).ok


def test_changing_any_field_is_detected() -> None:
    for field, value in [("event", "x"), ("actor", "x"), ("run_id", "x"), ("at", "x")]:
        rows = _chain(4)
        rows[1][field] = value
        report = verify_chain("t", rows, {"seq": 4, "hash": rows[-1]["hash"]})
        assert not report.ok and report.bad_seq == 2, field


def test_a_removed_middle_entry_is_detected() -> None:
    rows = _chain(4)
    del rows[1]
    report = verify_chain("t", rows, {"seq": 4, "hash": rows[-1]["hash"]})
    assert not report.ok and "missing" in (report.error or "")


def test_removed_newest_entries_are_detected_through_the_head() -> None:
    rows = _chain(4)
    report = verify_chain("t", rows[:3], {"seq": 4, "hash": rows[3]["hash"]})
    assert not report.ok and "newest" in (report.error or "")


def test_entries_with_no_head_are_detected() -> None:
    assert not verify_chain("t", _chain(2), None).ok


def test_a_chain_cannot_be_moved_to_another_tenant() -> None:
    rows = _chain(2)
    assert not verify_chain("other", rows, {"seq": 2, "hash": rows[-1]["hash"]}).ok


# -- against the database ------------------------------------------------------------------
def test_appended_entries_form_a_valid_chain(repo: Repo) -> None:
    _fill(repo, 6)
    report = repo.audit_verify("acme")
    assert report.ok and report.checked == 6 and report.head_seq == 6
    rows, total = repo.audit_search(tenant="acme")
    assert total == 6 and [r["seq"] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert rows[1]["hash"] != rows[0]["hash"]


def test_each_tenant_has_its_own_chain(repo: Repo) -> None:
    _fill(repo, 3, tenant="acme")
    _fill(repo, 2, tenant="globex")
    assert repo.audit_verify("acme").head_seq == 3
    assert repo.audit_verify("globex").head_seq == 2
    assert set(repo.audit_tenants()) == {"acme", "globex"}


def test_the_tenant_defaults_to_the_runs_own(repo: Repo) -> None:
    repo.upsert({"run_id": "r1", "status": "running"}, tenant="acme")
    repo.add_audit("r1", "created", "api")
    assert repo.audit_search(tenant="acme")[1] == 1
    assert repo.audit_search(tenant="default")[1] == 0


def test_a_run_without_a_stored_incident_audits_to_the_default_tenant(repo: Repo) -> None:
    repo.add_audit("ghost", "created", "api")
    assert repo.audit_search(tenant="default")[1] == 1


def test_editing_a_stored_entry_breaks_verification(repo: Repo) -> None:
    _fill(repo, 5)
    repo._exec(
        "UPDATE audit_log SET actor = ? WHERE tenant_id = ? AND seq = ?", ("mallory", "acme", 3)
    )
    report = repo.audit_verify("acme")
    assert not report.ok and report.bad_seq == 3 and report.error == "entry was modified"


def test_deleting_a_stored_entry_breaks_verification(repo: Repo) -> None:
    _fill(repo, 5)
    repo._exec("DELETE FROM audit_log WHERE tenant_id = ? AND seq = ?", ("acme", 2))
    assert not repo.audit_verify("acme").ok


def test_truncating_the_tail_breaks_verification(repo: Repo) -> None:
    _fill(repo, 5)
    repo._exec("DELETE FROM audit_log WHERE tenant_id = ? AND seq >= ?", ("acme", 4))
    report = repo.audit_verify("acme")
    assert not report.ok and "newest" in (report.error or "")


def test_rewriting_an_entry_and_its_hash_still_breaks_the_next_link(repo: Repo) -> None:
    _fill(repo, 4)
    forged = entry_hash("", canonical("acme", 1, "r0", "forged", "x", "{}", "2026-01-01"))
    repo._exec(
        "UPDATE audit_log SET event = ?, actor = ?, detail_json = ?, at = ?, hash = ? "
        "WHERE tenant_id = ? AND seq = ?",
        ("forged", "x", "{}", "2026-01-01", forged, "acme", 1),
    )
    report = repo.audit_verify("acme")
    assert not report.ok and report.bad_seq == 2


def test_concurrent_writers_cannot_fork_the_chain(repo: Repo) -> None:
    def write(n: int) -> None:
        for i in range(10):
            repo.add_audit("r", f"e{n}", f"w{n}", {"i": i}, tenant="acme")

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(write, range(6)))

    report = repo.audit_verify("acme")
    assert report.ok and report.checked == 60
    rows, _ = repo.audit_search(tenant="acme", limit=500)
    assert [r["seq"] for r in rows] == list(range(1, 61))


def test_search_filters(repo: Repo) -> None:
    repo.add_audit("r1", "decision:approved", "alice", tenant="acme")
    repo.add_audit("r1", "decision:rejected", "bob", tenant="acme")
    repo.add_audit("r2", "created", "alice", tenant="acme")
    repo.add_audit("", "auth:denied", "anonymous", {"why": "x"}, tenant=SYSTEM_TENANT)

    def count(**filters: Any) -> int:
        return repo.audit_search(tenant="acme", **filters)[1]

    assert count() == 3
    assert count(run_id="r1") == 2
    assert count(event="decision") == 2  # a group matches its members
    assert count(event="decision:approved") == 1
    assert count(event="deci") == 0  # not a raw prefix match
    assert count(actor="alice") == 2
    assert count(event="decision", actor="bob") == 1
    assert repo.audit_search(tenant=SYSTEM_TENANT)[1] == 1
    assert repo.audit_search(tenant=None)[1] == 4  # unscoped, for operators


def test_search_time_window_and_paging(repo: Repo) -> None:
    _fill(repo, 6)
    rows, _ = repo.audit_search(tenant="acme", limit=500)
    middle = rows[3]["at"]
    # timestamps are milliseconds, so entries written together can share one: count, don't assume
    assert repo.audit_search(tenant="acme", since=middle)[1] == sum(r["at"] >= middle for r in rows)
    assert repo.audit_search(tenant="acme", until=rows[0]["at"])[1] == 0
    page, total = repo.audit_search(tenant="acme", limit=2, offset=2)
    assert total == 6 and [r["seq"] for r in page] == [3, 4]


def test_search_returns_parsed_detail(repo: Repo) -> None:
    repo.add_audit("r", "e", "a", {"nested": {"k": [1, 2]}}, tenant="acme")
    assert repo.audit_search(tenant="acme")[0][0]["detail"] == {"nested": {"k": [1, 2]}}


def test_prune_removes_old_entries_and_the_chain_still_verifies(repo: Repo) -> None:
    _fill(repo, 6)
    rows, _ = repo.audit_search(tenant="acme", limit=500)
    cutoff = rows[3]["at"]  # entries strictly before it go: seq 1..k

    removed = repo.audit_prune(cutoff, tenant="acme")

    left, total = repo.audit_search(tenant="acme", limit=500)
    assert removed >= 1 and total == 6 - removed
    assert repo.audit_verify("acme").ok
    repo.add_audit("r", "after-prune", "a", tenant="acme")
    report = repo.audit_verify("acme")
    assert report.ok and report.head_seq == 7
    assert left[0]["seq"] == removed + 1


def test_prune_without_a_tenant_covers_every_chain_and_keeps_newer_entries(repo: Repo) -> None:
    _fill(repo, 3, tenant="acme")
    _fill(repo, 3, tenant="globex")
    assert repo.audit_prune("9999-01-01") == 6
    assert repo.audit_search(tenant=None)[1] == 0
    assert repo.audit_verify("acme").ok and repo.audit_verify("globex").ok
    assert repo.audit_prune("0001-01-01") == 0


def test_tampering_after_a_prune_is_still_caught(repo: Repo) -> None:
    _fill(repo, 6)
    rows, _ = repo.audit_search(tenant="acme", limit=500)
    repo.audit_prune(rows[2]["at"], tenant="acme")
    remaining, _ = repo.audit_search(tenant="acme", limit=500)
    repo._exec(
        "UPDATE audit_log SET event = ? WHERE tenant_id = ? AND seq = ?",
        ("edited", "acme", remaining[-1]["seq"]),
    )
    assert not repo.audit_verify("acme").ok


def test_entries_from_before_the_chain_existed_are_ignored(repo: Repo) -> None:
    repo._exec(
        "INSERT INTO audit_log (run_id, event, actor, detail_json, at) VALUES (?, ?, ?, ?, ?)",
        ("old", "created", "api", "{}", "2020-01-01T00:00:00Z"),
    )
    repo.add_audit("new", "created", "api", tenant="default")
    assert repo.audit_search(tenant="default")[1] == 1  # legacy rows have seq 0
    assert repo.audit_verify("default").ok
    assert repo.audit_for("old")[0]["event"] == "created"  # but the per-run trail still shows it
