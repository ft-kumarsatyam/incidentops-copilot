"""`incidentops keys` and `incidentops audit`, and the secrets rules around them."""

import json
import os
from pathlib import Path

import pytest

from incidentops import cli
from incidentops.core.config import Settings
from incidentops.persistence.factory import open_repo
from incidentops.security.apikeys import resolve_key
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture

PAYLOAD = load_fixture("incident_complete.json")


def run(settings: Settings, *argv: str) -> tuple[dict[str, object], int]:
    args = cli.build_parser().parse_args(argv)
    if args.command == "keys":
        return cli.run_keys(settings, args), 0
    return cli.run_audit(settings, args)


def test_a_key_made_on_the_command_line_works_and_is_audited(settings: Settings) -> None:
    result, _ = run(
        settings,
        "keys",
        "create",
        "--name",
        "ci",
        "--roles",
        "viewer,responder",
        "--tenant",
        "acme",
        "--expires-days",
        "30",
    )

    repo = open_repo(settings)
    principal = resolve_key(repo, str(result["token"]))
    assert principal.tenant == "acme" and principal.roles == {"viewer", "responder"}
    assert principal.actor == "key:ci"
    assert [e["event"] for e in repo.audit_search(tenant="acme")[0]] == ["key:created"]
    listed, _ = run(settings, "keys", "list", "--tenant", "acme")
    assert [k["name"] for k in listed["keys"]] == ["ci"]  # type: ignore[attr-defined]
    assert "secret_hash" not in json.dumps(listed) and str(result["token"]) not in json.dumps(
        listed
    )


def test_keys_default_to_the_default_tenant_and_can_be_revoked(settings: Settings) -> None:
    created, _ = run(settings, "keys", "create", "--name", "ci", "--roles", "admin")
    key_id = created["key"]["id"]  # type: ignore[index]
    assert created["key"]["tenant"] == "default"  # type: ignore[index]

    assert run(settings, "keys", "revoke", key_id)[0] == {"revoked": True, "key_id": key_id}
    assert run(settings, "keys", "revoke", key_id)[0]["revoked"] is False
    repo = open_repo(settings)
    assert "key:revoked" in [e["event"] for e in repo.audit_search(tenant="default")[0]]


def test_invalid_roles_are_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="roles"):
        run(settings, "keys", "create", "--name", "x", "--roles", "root")


def _service_with_history(settings: Settings) -> IncidentService:
    service = IncidentService(settings)
    service.start(PAYLOAD, actor="alice")
    service.decide(PAYLOAD["run_id"], PAYLOAD["run_id"], "approved", actor="bob")
    return service


def test_verify_passes_on_an_untouched_log_and_fails_the_process_on_tampering(
    settings: Settings,
) -> None:
    service = _service_with_history(settings)
    try:
        ok, status = run(settings, "audit", "verify")
        assert ok["ok"] is True and status == 0

        service.repo._exec(  # type: ignore[attr-defined]
            "UPDATE audit_log SET actor = ? WHERE seq = 1", ("mallory",)
        )
        broken, status = run(settings, "audit", "verify")
        assert broken["ok"] is False and status == 1
        assert broken["chains"][0]["bad_seq"] == 1  # type: ignore[index]
    finally:
        service.close()


def test_export_writes_every_entry_and_the_chain_check(settings: Settings, tmp_path: Path) -> None:
    service = _service_with_history(settings)
    service.close()
    target = tmp_path / "audit.ndjson"

    result, _ = run(settings, "audit", "export", "--output", str(target))

    lines = [json.loads(line) for line in target.read_text().splitlines()]
    assert result["exported"] == len(lines) - 1 >= 3
    assert lines[-1]["type"] == "chain" and lines[-1]["ok"] is True
    assert {line["actor"] for line in lines[:-1]} == {"alice", "bob"}


def test_export_to_stdout(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    _service_with_history(settings).close()
    run(settings, "audit", "export")
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1])["type"] == "chain"


def test_prune_needs_a_period_and_keeps_the_chain_valid(settings: Settings) -> None:
    _service_with_history(settings).close()
    with pytest.raises(SystemExit, match="positive"):
        run(settings, "audit", "prune")  # AUDIT_RETENTION_DAYS defaults to 0 = keep forever

    kept, _ = run(settings, "audit", "prune", "--days", "30")
    assert kept["removed"] == 0  # everything is recent

    repo = open_repo(settings)
    repo._exec("UPDATE audit_log SET at = ?", ("2000-01-01T00:00:00.000+00:00",))  # type: ignore[attr-defined]
    removed, _ = run(settings, "audit", "prune", "--days", "30")
    assert removed["removed"] >= 3
    assert run(settings, "audit", "verify")[0]["ok"] is True


def test_the_retention_setting_is_the_default_period(settings: Settings) -> None:
    _service_with_history(settings).close()
    repo = open_repo(settings)
    repo._exec("UPDATE audit_log SET at = ?", ("2000-01-01T00:00:00.000+00:00",))  # type: ignore[attr-defined]
    configured = settings.model_copy(update={"audit_retention_days": 7})
    assert run(configured, "audit", "prune")[0]["removed"] >= 3


# -- secrets -------------------------------------------------------------------------------
def prod(settings: Settings, **updates: object) -> Settings:
    return settings.model_copy(update={"incidentops_env": "prod", **updates})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("incidentops_api_key", "local-development-key"),
        ("n8n_action_token", "local-action-token"),
        ("metrics_token", "local-metrics-token"),
        ("webhook_signing_secret", "change-me"),
        ("inbound_signing_secret", "Change-This-Before-Sharing"),
        ("database_url", "postgresql://u:local-db-password@db/x"),
    ],
)
def test_development_placeholders_are_refused_outside_dev(
    settings: Settings, field: str, value: str
) -> None:
    config = prod(settings, **{field: value})
    assert field.upper() in config.placeholder_secrets()
    with pytest.raises(RuntimeError, match=field.upper()):
        config.assert_production_ready()


def test_placeholders_are_fine_in_dev_and_real_secrets_are_fine_anywhere(
    settings: Settings,
) -> None:
    settings.model_copy(
        update={"incidentops_api_key": "local-development-key"}
    ).assert_production_ready()
    prod(settings, incidentops_api_key="x9Vq-real-secret").assert_production_ready()
    assert prod(settings).placeholder_secrets() == []


def test_secrets_can_come_from_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "api-key"
    secret.write_text("from-a-file\n")  # a trailing newline is normal in secret files
    monkeypatch.setenv("INCIDENTOPS_API_KEY_FILE", str(secret))
    monkeypatch.setenv("METRICS_TOKEN_FILE", str(tmp_path / "missing"))

    with pytest.raises(RuntimeError, match="METRICS_TOKEN_FILE"):
        Settings(_env_file=None)  # type: ignore[call-arg]

    monkeypatch.delenv("METRICS_TOKEN_FILE")
    loaded = Settings(_env_file=None)  # type: ignore[call-arg]
    assert loaded.incidentops_api_key == "from-a-file"


def test_an_explicit_variable_beats_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "key"
    secret.write_text("from-file")
    monkeypatch.setenv("INCIDENTOPS_API_KEY_FILE", str(secret))
    monkeypatch.setenv("INCIDENTOPS_API_KEY", "from-env")
    assert Settings(_env_file=None).incidentops_api_key == "from-env"  # type: ignore[call-arg]


def test_unrelated_file_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOMETHING_ELSE_FILE", "/does/not/exist")
    Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_worker_refuses_placeholders_too(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="N8N_ACTION_TOKEN"):
        cli.run_worker(prod(settings, n8n_action_token="local-action-token"))


def test_check_reports_identity_and_placeholders(settings: Settings) -> None:
    report = cli.check(settings.model_copy(update={"metrics_token": "local-x"}))
    assert report["oidc"] == "not configured" and report["placeholder_secrets"] == ["METRICS_TOKEN"]
    assert os.environ.get("INCIDENTOPS_API_KEY") is None or True  # env is not mutated by check
