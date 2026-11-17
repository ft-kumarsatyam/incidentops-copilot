from typing import Any

import httpx
import pytest

from incidentops.tools.github import fetch_recent_commit


def _patch_get(monkeypatch: pytest.MonkeyPatch, payload: list[dict[str, Any]]) -> None:
    def get(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)


def test_returns_latest_commit_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(
        monkeypatch,
        [{"sha": "abc", "commit": {"message": "Fix cart\n\nbody"}, "html_url": "https://x/abc"}],
    )

    assert fetch_recent_commit("acme/checkout", "t") == {
        "repo": "acme/checkout",
        "recent_deploy": "abc",
        "message": "Fix cart",
        "url": "https://x/abc",
    }


def test_empty_repository_returns_empty_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, [])
    assert fetch_recent_commit("acme/checkout", "t") == {}
