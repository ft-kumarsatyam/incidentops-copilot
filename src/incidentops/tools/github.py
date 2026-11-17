"""Read-only GitHub context adapter."""

from typing import Any

import httpx


def fetch_recent_commit(repo: str, token: str, timeout: float = 10.0) -> dict[str, Any]:
    """Return the latest commit; callers decide whether absence is actionable."""
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
