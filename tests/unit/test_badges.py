"""The badge generator reads the real reports, so a format change in them must fail loudly."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "update_badges", ROOT / "scripts/update_badges.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


badges = _load()


def test_incident_report_row_is_parsed() -> None:
    assert badges.parse_incident_evals("| rules | 37/37 | 37/37 | 9/9 |") == (37, 37)
    assert badges.parse_incident_evals("| claude | 8/10 | x |") == (8, 10)


def test_guardrail_rows_are_parsed() -> None:
    text = (
        "| Attack block rate | 152/152 (100.0%) | 100% |\n"
        "| Benign pass rate (1 - false positives) | 29/29 (100.0%) | 100% |\n"
    )
    assert badges.parse_guardrails(text) == (152, 152, 29, 29)


def test_retrieval_rewrite_row_is_parsed() -> None:
    text = "| fts | 81.82% | 0.8 |\n| rewrite | 100.00% | 1.0 |\n| hybrid | 100.00% | 1.0 |\n"
    assert badges.parse_retrieval_rewrite(text) == 100.0


@pytest.mark.parametrize(
    ("parser", "text"),
    [
        (badges.parse_incident_evals, "no table here"),
        (badges.parse_guardrails, "| Attack block rate | n/a |"),
        (badges.parse_retrieval_rewrite, "| fts | 80% |"),
    ],
)
def test_an_unreadable_report_raises_instead_of_writing_a_wrong_badge(parser, text) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        parser(text)


def test_colors_reflect_the_result() -> None:
    assert badges.ratio_color(37, 37) == badges.GREEN
    assert badges.ratio_color(35, 37) == badges.AMBER
    assert badges.ratio_color(10, 37) == badges.RED


def test_committed_badges_match_the_committed_reports() -> None:
    """If this fails, run `make badges`: the reports changed but the badges were not refreshed."""
    for name, payload in badges.build().items():
        committed = ROOT / "assets/badges" / name
        assert committed.exists(), f"{name} missing: run `make badges`"
        on_disk = json.loads(committed.read_text(encoding="utf-8"))
        # The test count moves with every added test, so only its presence is pinned here.
        if name == "tests.json":
            assert on_disk["label"] == payload["label"]
        else:
            assert on_disk == payload, f"{name} is stale: run `make badges`"
