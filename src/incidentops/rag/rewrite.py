"""Deterministic query expansion before lexical / hybrid search."""

from __future__ import annotations

import re
from typing import Any

WORD = re.compile(r"[A-Za-z0-9_]{2,}")

SERVICE_ALIASES: dict[str, tuple[str, ...]] = {
    "checkout-api": (
        "checkout",
        "payment",
        "cart",
        "purchase",
        "charges",
        "5xx",
        "error",
        "deploy",
        "rollback",
    ),
    "auth-api": (
        "authentication",
        "auth",
        "sign-in",
        "login",
        "identity",
        "token",
        "session",
        "oauth",
    ),
}

SYMPTOM_PHRASES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("complete", "purchase"), ("checkout", "payment", "cart", "5xx")),
    (("cannot", "checkout"), ("checkout", "payment", "5xx")),
    (("login", "broken"), ("sign-in", "authentication", "auth")),
    (("oauth",), ("authentication", "sign-in", "identity")),
    (("five", "hundred"), ("5xx", "error", "rate")),
    (("token", "refresh"), ("authentication", "session", "sign-in")),
    (("feature", "flag"), ("mitigation", "disable", "auth")),
)


def expand_runbook_query(
    query: str,
    *,
    service: str = "",
    symptoms: list[str] | None = None,
) -> str:
    """Return a wider query string; original tokens are always kept."""
    parts: list[str] = [query.strip()]
    slug = service.strip().lower()
    if slug:
        parts.extend(SERVICE_ALIASES.get(slug, (slug.replace("-", " "),)))
    lowered = " ".join(parts).lower()
    if symptoms:
        lowered = f"{lowered} {' '.join(symptoms).lower()}"
        parts.extend(symptoms)
    tokens = set(WORD.findall(lowered))
    for required, extras in SYMPTOM_PHRASES:
        if all(term in tokens or term in lowered for term in required):
            parts.extend(extras)
    seen: set[str] = set()
    ordered: list[str] = []
    for piece in parts:
        for term in WORD.findall(piece):
            key = term.lower()
            if key not in seen:
                seen.add(key)
                ordered.append(term)
    return " ".join(ordered) if ordered else query.strip()


def rewrite_metadata(
    original: str,
    expanded: str,
    service: str,
) -> dict[str, Any]:
    if expanded == original:
        return {}
    return {
        "runbook_query_original": original[:500],
        "runbook_query_expanded": expanded[:500],
        "runbook_query_service": service[:128],
    }
