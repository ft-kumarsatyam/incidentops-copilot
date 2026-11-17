"""Deterministic checks that every model answer must pass before the workflow uses it.

The model proposes; this module decides what survives. Nothing here calls a model, so a
manipulated or buggy model cannot talk its way past it. The layers, in order:

1. ``injection_signals``  flags hostile instructions in the *input* (surfaced, never trusted).
2. ``validate_triage``    drops unsafe or ungrounded actions, redacts and bounds all text,
                          and cross-checks the model's severity against measured impact.
3. ``validate_verdict``   bounds and redacts the critic's free text.

Anything that leaves a triage with no usable action raises ``GuardrailError`` so the caller
falls back to the deterministic rules provider.
"""

import math
import re
import unicodedata

from incidentops.agents.schemas import (
    ActionType,
    CriticVerdict,
    GuardrailError,
    ProposedAction,
    Severity,
    Triage,
    TriageContext,
)
from incidentops.core.logging import get_logger
from incidentops.core.redact import redact_text
from incidentops.tools.monitoring import parse_error_rate

log = get_logger(__name__)

MAX_ACTIONS = 8
MAX_HYPOTHESES = 5
MAX_CONFLICTS = 5
MAX_ISSUES = 10
MAX_TEXT = 300
MAX_REASONING = 1200

_SEVERITY_RANK: dict[str, int] = {"SEV-1": 1, "SEV-2": 2, "SEV-3": 3}

# Evidence an action type must cite beyond the generic "cites something that exists" rule.
_REQUIRED_EVIDENCE: dict[ActionType, str] = {
    "rollback": "github",
    "scale": "monitoring",
}
# Action types that are not allowed for a given severity (paging for SEV-3 is over-escalation).
_FORBIDDEN_AT_SEVERITY: dict[ActionType, set[Severity]] = {"page": {"SEV-3"}}


# -- text helpers ---------------------------------------------------------------------------
_INVISIBLE = dict.fromkeys([*range(0x200B, 0x2010), 0x2060, 0xFEFF, 0x00AD, 0x180E], None)


def strip_invisible(text: str) -> str:
    """NFKC-normalize and drop zero-width characters, so they cannot split an email or URL."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE)


def fold(text: str, *, keep_lines: bool = False) -> str:
    """Canonical form for matching: NFKC, no zero-width/control chars, casefolded, one space."""
    text = strip_invisible(text)
    lines = [
        " ".join(
            "".join(ch for ch in line if unicodedata.category(ch)[0] != "C").casefold().split()
        )
        for line in (text.splitlines() or [text])
    ]
    return "\n".join(lines) if keep_lines else " ".join(lines)


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return " ".join(text.split())[:limit]


def _clean(text: str, limit: int = MAX_TEXT) -> str:
    """Redact secrets, collapse whitespace, bound length."""
    return _clip(redact_text(text), limit)


# -- layer 1: input injection signals -------------------------------------------------------
_INJECTION = tuple(
    re.compile(pattern, re.MULTILINE)
    for pattern in (
        r"\bignore (?:all |any |the |your )?(?:previous|prior|above|earlier|preceding|former)"
        r" (?:instructions?|rules?|prompts?|context|guidelines?)",
        r"\bdisregard (?:all |any |the |your )?(?:previous|prior|above|earlier|system)",
        r"\bforget (?:all |everything |your |the )+(?:previous |prior |above |earlier )?"
        r"(?:instructions|rules|prompts?|guidelines)",
        r"\bforget everything (?:you|above|before)\b",
        r"\byou are now (?:an? |the )?"
        r"(?:unrestricted|unfiltered|jailbroken|admin\w*|root|dan|evil)\b",
        r"\bnew instructions?\s*[:\-]",
        r"\b(?:reveal|print|show|repeat|leak|output) (?:me )?(?:your|the) (?:system )?"
        r"(?:prompt|instructions)",
        r"(?:^|[\n.;!?])\s*(?:system|assistant|developer)\s*:",
        r"</?\s*incident\s*>",
        r"\[/?inst\]|<\|im_(?:start|end)\|>|<\|system\|>",
        r"\bjailbreak\b|\bdeveloper mode\b|\bdan mode\b",
        r"\byou must (?:now )?(?:add|run|execute|page|delete|drop|wipe|ignore|call)\b",
        r"\bdo not (?:follow|obey|use) (?:the |your )?(?:rules|instructions|guidelines)",
        r"\boverride (?:the |all )?(?:safety|guardrails?|rules|instructions)\b",
    )
)


def has_injection(text: str) -> bool:
    folded = fold(text, keep_lines=True)
    return any(pattern.search(folded) for pattern in _INJECTION)


def injection_signals(context: TriageContext) -> list[str]:
    """Names of the incident fields that look like they contain instructions for a model."""
    fields: dict[str, str] = {
        "summary": context.summary,
        "service": context.service,
        "owner": context.owner,
        "monitoring.alert": str(context.monitoring.get("alert", "")),
        "github.message": str(context.github.get("message", "")),
    }
    fields.update({f"symptoms[{i}]": text for i, text in enumerate(context.symptoms)})
    return [name for name, text in fields.items() if text and has_injection(text)]


# -- layer 2: model output policy -----------------------------------------------------------
_UNSAFE: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (rule, re.compile(pattern))
    for rule, pattern in (
        ("destructive_command", r"\brm\s+-\w*[rf]\w*\b"),
        ("destructive_command", r"\b(?:mkfs|fdisk|shred)\b|\bdd\s+if=|:\(\)\s*\{"),
        ("destructive_command", r"\bchmod\s+(?:-r\s+)?0?777\b"),
        ("destructive_command", r"\bdrop\s+(?:table|database|schema|index)\b"),
        ("destructive_command", r"\btruncate\s+table\b|\bdelete\s+from\s+[\w.]+\s*(?:where|;|$)"),
        ("destructive_command", r"\bkubectl\s+delete\b|\bterraform\s+destroy\b"),
        (
            "destructive_command",
            r"\bgit\s+push\s+(?:\S+\s+)*(?:-f|--force)\b|\bgit\s+reset\s+--hard",
        ),
        ("destructive_command", r"\bflush(?:all|db)\b|\bformat\s+c:"),
        ("remote_code", r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"),
        ("remote_code", r"\beval\s*\(|\bexec\s*\(|\bpowershell\b.*-enc|\bbase64\s+-d\b"),
        ("markup", r"<\s*(?:script|iframe|object|embed)\b|javascript:|\bon(?:error|load)\s*="),
    )
) + tuple(("injection_echo", pattern) for pattern in _INJECTION)

_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,255}\.[\w.-]{1,255}")
_URL = re.compile(r"(?:https?://|www\.)[^\s)>\]\"']+")
_PRIVILEGED = re.compile(
    r"\b(?:ceo|cto|cfo|coo|ciso|board of directors|press release|the press|journalists?"
    r"|regulators?|law enforcement|all customers|every customer)\b"
)


def _trusted_text(context: TriageContext) -> str:
    """Text from sources we control: runbooks. Used to allow terms and URLs they contain."""
    return fold(
        " ".join(f"{hit.get('title', '')} {hit.get('excerpt', '')}" for hit in context.runbook_hits)
    )


def _trusted_urls(context: TriageContext) -> set[str]:
    urls = set(
        _URL.findall(
            " ".join(
                f"{hit.get('title', '')} {hit.get('excerpt', '')}" for hit in context.runbook_hits
            )
        )
    )
    github_url = context.github.get("url")
    if isinstance(github_url, str):
        urls.add(github_url)
    return {url.rstrip(".,;:") for url in urls}


def policy_violation(text: str, context: TriageContext) -> str | None:
    """Return the name of the first rule ``text`` breaks, or ``None`` if it is acceptable."""
    folded = fold(text, keep_lines=True)
    for rule, pattern in _UNSAFE:
        if pattern.search(folded):
            return rule
    visible = strip_invisible(text)
    if _EMAIL.search(visible):
        return "exfiltration"
    trusted_urls = _trusted_urls(context)
    for url in _URL.findall(visible):
        if url.rstrip(".,;:") not in trusted_urls:
            return "exfiltration"
    trusted = _trusted_text(context)
    for match in _PRIVILEGED.finditer(folded):
        if match.group() not in trusted:
            return "privileged_target"
    return None


def _is_grounded(action: ProposedAction, context: TriageContext) -> bool:
    if not action.evidence or not set(action.evidence) <= context.evidence_refs:
        return False
    required = _REQUIRED_EVIDENCE.get(action.type)
    if required and required not in action.evidence:
        return False  # e.g. a rollback must cite the deploy it would revert
    if action.type == "rollback" and not context.github.get("recent_deploy"):
        return False  # never suggest a rollback without a deploy to roll back
    if action.type == "follow_runbook" and not any(
        ref.startswith("runbook:") for ref in action.evidence
    ):
        return False
    return True


# -- severity -------------------------------------------------------------------------------
def measured_severity(context: TriageContext) -> Severity:
    """Severity implied by the numbers alone. The model may differ, but must not contradict."""
    error_rate = parse_error_rate(context.monitoring.get("error_rate")) or 0.0
    symptoms = " ".join(context.symptoms).lower()
    if error_rate >= 0.1 or "outage" in symptoms:
        return "SEV-1"
    if error_rate < 0.03:
        return "SEV-3"
    return "SEV-2"


def deterministic_conflicts(context: TriageContext) -> list[str]:
    declared = str(context.monitoring.get("severity", "")).upper()
    if declared in {"SEV-3", "LOW"} and measured_severity(context) == "SEV-1":
        return ["monitoring severity conflicts with measured impact"]
    return []


def _severity_conflict(model: Severity, context: TriageContext) -> str | None:
    """A model may not under-call measured impact, nor over-call it by two levels."""
    measurement = parse_error_rate(context.monitoring.get("error_rate"))
    if measurement is None or context.monitoring.get("timed_out"):
        return None  # unverified impact: the confidence cap already routes this to a human
    measured = measured_severity(context)
    gap = _SEVERITY_RANK[model] - _SEVERITY_RANK[measured]
    if gap > 0 or gap <= -2:
        return f"model severity ({model}) conflicts with measured impact ({measured})"
    return None


# -- entry points ---------------------------------------------------------------------------
def _safe_confidence(value: float) -> float:
    number = float(value)
    if math.isnan(number):
        return 0.0  # an unparseable confidence must fail closed, not become 1.0
    return max(0.0, min(1.0, number))


def _screened_text(items: list[str], context: TriageContext, *, kind: str) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    for item in items:
        raw = _clip(item)  # judged before redaction, so an email is seen as an email
        rule = policy_violation(raw, context) if raw else None
        text = _clean(item)
        if rule:
            log.warning("guardrail.dropped", kind=kind, rule=rule)
        elif text and text.lower() not in seen:
            seen.add(text.lower())
            kept.append(text)
    return kept


def validate_triage(triage: Triage, context: TriageContext) -> Triage:
    """Return a safe, bounded triage or raise ``GuardrailError``."""
    seen: set[tuple[str, str]] = set()
    actions: list[ProposedAction] = []
    for action in triage.actions:
        description = _clean(action.description)
        key = (action.type, description.lower())
        if not description or key in seen:
            continue
        if (
            action.type in _FORBIDDEN_AT_SEVERITY
            and triage.severity in _FORBIDDEN_AT_SEVERITY[action.type]
        ):
            log.warning("guardrail.dropped", kind="action", rule="severity_policy")
            continue
        if not _is_grounded(action, context):
            log.warning("guardrail.dropped", kind="action", rule="ungrounded")
            continue
        rule = policy_violation(_clip(action.description), context)
        if rule:
            log.warning("guardrail.dropped", kind="action", rule=rule)
            continue
        seen.add(key)
        actions.append(
            ProposedAction(
                type=action.type,
                description=description,
                evidence=list(dict.fromkeys(action.evidence)),
            )
        )
    if not actions:
        raise GuardrailError("no proposed action was grounded in the available evidence")

    conflicts = [_clean(item) for item in triage.conflicts if item.strip()][:MAX_CONFLICTS]
    mismatch = _severity_conflict(triage.severity, context)
    if mismatch and mismatch not in conflicts:
        conflicts.append(mismatch)

    return Triage(
        severity=triage.severity,
        confidence=_safe_confidence(triage.confidence),
        hypotheses=_screened_text(triage.hypotheses, context, kind="hypothesis")[:MAX_HYPOTHESES],
        actions=actions[:MAX_ACTIONS],
        conflicts=conflicts,
        reasoning=_clean(triage.reasoning, MAX_REASONING),
    )


def validate_verdict(verdict: CriticVerdict) -> CriticVerdict:
    """Bound and redact the critic's free text. The verdict itself is a closed enum."""
    issues = [_clean(item) for item in verdict.issues if item.strip()][:MAX_ISSUES]
    return CriticVerdict(verdict=verdict.verdict, issues=issues, notes=_clean(verdict.notes, 500))
