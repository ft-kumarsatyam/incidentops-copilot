"""Read-only GitHub context adapter."""

import re
from typing import Any

import httpx

REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
GITHUB_URL = "https://github.com/"
MAX_TEXT = 300


def is_valid_repo(repo: Any) -> bool:
    """``owner/name`` only. The value is interpolated into an API path, so no traversal."""
    return isinstance(repo, str) and bool(REPO.match(repo)) and ".." not in repo


def normalize_github(payload: dict[str, Any]) -> dict[str, Any]:
    """Allowlist the fields the workflow understands; drop anything else the caller sent."""
    if not payload or not isinstance(payload, dict):
        return {}
    result: dict[str, Any] = {}
    if is_valid_repo(payload.get("repo")):
        result["repo"] = payload["repo"]
    for key in ("recent_deploy", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = " ".join(value.split())[:MAX_TEXT]
    url = payload.get("url")
    if isinstance(url, str) and url.startswith(GITHUB_URL) and len(url) <= 500:
        result["url"] = url
    return result


def fetch_recent_commit(repo: str, token: str, timeout: float = 10.0) -> dict[str, Any]:
    """Return the latest commit; callers decide whether absence is actionable."""
    if not is_valid_repo(repo):
        raise ValueError("repo must look like owner/name")
    response = httpx.get(
        f"https://api.github.com/repos/{repo}/commits",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        params={"per_page": 1},
        timeout=timeout,
    )
    response.raise_for_status()
    commits = response.json()
    if not commits:
        return {}
    commit = commits[0]
    return {
        "repo": repo,
        "recent_deploy": commit["sha"],
        "message": commit["commit"]["message"].splitlines()[0],
        "url": commit["html_url"],
    }
