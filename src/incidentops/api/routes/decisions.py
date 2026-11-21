"""Ask the Jev decision model a typed question, and see how it is configured and used.

Both routes are RBAC-gated (``decision:invoke`` for responders and up, ``decision:admin`` for
admins), and every call is audited. The audit entry records who asked, which questions and how
many tokens it cost, never the text that was sent.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from incidentops.api.deps import get_service, require
from incidentops.decision import JevClient, JevError
from incidentops.security import Principal
from incidentops.workflows.incident.service import IncidentService

router = APIRouter(tags=["decisions"])
Service = Annotated[IncidentService, Depends(get_service)]
Invoker = Annotated[Principal, Depends(require("decision:invoke"))]
Admin = Annotated[Principal, Depends(require("decision:admin"))]

MAX_QUESTIONS = 8
Text = Annotated[str, Field(max_length=2000)]


class Question(BaseModel):
    type: Literal["noul", "choice", "score"]
    instructions: Text
    criteria: dict[str, Text] | list[Text] | None = None

    @model_validator(mode="after")
    def _criteria_fit_the_type(self) -> "Question":
        criteria = self.criteria
        if self.type == "choice" and not (isinstance(criteria, dict) and 1 <= len(criteria) <= 64):
            raise ValueError("a choice question needs 1 to 64 named criteria")
        if self.type == "score" and not (isinstance(criteria, list) and 1 <= len(criteria) <= 10):
            raise ValueError("a score question needs 1 to 10 ordered criteria")
        if self.type == "noul" and not (
            criteria is None or (isinstance(criteria, dict) and set(criteria) <= {"true", "false"})
        ):
            raise ValueError("a noul question takes only 'true' and 'false' criteria")
        return self


class DecisionRequest(BaseModel):
    state: str | dict[str, Any] | list[Any] = Field(
        description="What the questions are about. Secrets and PII are redacted before it is sent."
    )
    questions: dict[Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")], Question] = Field(
        min_length=1, max_length=MAX_QUESTIONS
    )


def _client(service: IncidentService) -> JevClient:
    if service.jev is None:
        raise HTTPException(status_code=503, detail="the decision model is not enabled")
    return service.jev


def _check_tenant(service: IncidentService, principal: Principal) -> None:
    allowed = service.settings.jev_allowed_tenants
    if allowed and principal.tenant not in allowed:
        service.repo.add_audit(
            "",
            "decision:forbidden",
            principal.actor,
            {"reason": "tenant not allowed"},
            tenant=principal.tenant,
        )
        raise HTTPException(status_code=403, detail="the decision model is not enabled for you")


@router.post("/decisions")
def decide(request: DecisionRequest, service: Service, principal: Invoker) -> dict[str, Any]:
    client = _client(service)
    _check_tenant(service, principal)
    questions = {name: q.model_dump(exclude_none=True) for name, q in request.questions.items()}
    try:
        data = client.evaluate(request.state, questions)
    except JevError as error:
        service.repo.add_audit(
            "",
            "decision:failed",
            principal.actor,
            {"questions": sorted(questions), "error": str(error)},
            tenant=principal.tenant,
        )
        raise HTTPException(status_code=502, detail="the decision model call failed") from error
    usage = data.get("usage") or {}
    service.repo.add_audit(
        "",
        "decision:invoked",
        principal.actor,
        {
            "model": data.get("model"),
            "questions": sorted(questions),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        },
        tenant=principal.tenant,
    )
    return {"model": data.get("model"), "answers": data["answers"], "usage": usage}


@router.get("/decisions/status")
def status(service: Service, principal: Admin) -> dict[str, Any]:
    settings = service.settings
    client = service.jev
    return {
        "enabled": client is not None,
        "model": settings.jev_model,
        "allowed_tenants": settings.jev_allowed_tenants,
        "min_confidence": settings.jev_min_confidence,
        "injection_threshold": settings.jev_injection_threshold,
        "calls": client.calls if client else 0,
        "errors": client.errors if client else 0,
        "input_tokens": client.input_tokens if client else 0,
        "output_tokens": client.output_tokens if client else 0,
    }
