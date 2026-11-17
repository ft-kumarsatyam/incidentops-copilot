import json
from pathlib import Path

from workflow_automation.config import Settings
from workflow_automation.tools.runbooks import index_runbooks, search_runbooks
from workflow_automation.workflows.incident import run_incident


def test_index_and_search_markdown_runbooks(tmp_path: Path) -> None:
    source = tmp_path / "runbooks"
    source.mkdir()
    (source / "payments.md").write_text(
        "# Payments\n\n## Duplicate charges\n\nPause retries and inspect idempotency keys.",
        encoding="utf-8",
    )
    database = tmp_path / "runbooks.db"

    assert index_runbooks(source, database) == 1
    hits = search_runbooks(database, "payment duplicate idempotency")

    assert hits[0]["path"] == "payments.md"
    assert hits[0]["title"] == "Duplicate charges"
    assert "idempotency" in hits[0]["excerpt"]


def test_incident_actions_cite_retrieved_runbook(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    database = tmp_path / "runbooks.db"
    index_runbooks(root / "runbooks", database)
    incident = json.loads(
        (Path(__file__).parent / "fixtures" / "incident_complete.json").read_text(
            encoding="utf-8"
        )
    )

    result = run_incident(
        incident,
        settings=Settings(checkpoint_dir=tmp_path / "checkpoints", runbook_db=database),
    )

    assert result["runbook_hits"][0]["path"] == "checkout-api.md"
    assert any("checkout-api.md" in action for action in result["proposed_actions"])

