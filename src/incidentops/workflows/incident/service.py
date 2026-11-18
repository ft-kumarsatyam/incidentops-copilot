"""Application service: runs, resumes and dispatches incidents.

The compiled LangGraph (with a SQLite checkpointer) owns workflow state. This service is the
only place that talks to the graph, keeps the queryable read-model in sync, writes the audit
log, and dispatches approved actions to n8n.
"""

from collections.abc import Callable
from typing import Any

import httpx
from langgraph.types import Command

from incidentops.agents.factory import build_provider
from incidentops.agents.resilient import TriageProvider
from incidentops.api.events import build_event_bus
from incidentops.core.config import Settings
from incidentops.core.errors import IncidentConflictError, IncidentNotFoundError
from incidentops.core.logging import bound_context, get_logger
from incidentops.core.redact import redact
from incidentops.core.signing import host_allowed
from incidentops.core.telemetry import span
from incidentops.persistence.base import Repo
from incidentops.persistence.factory import open_storage
from incidentops.tools.n8n import request_actions as request_n8n_actions
from incidentops.tools.zapier import request_actions as request_zapier_actions
from incidentops.workflows.incident.graph import build_incident_graph
from incidentops.workflows.incident.nodes import stable_id

log = get_logger(__name__)

Dispatcher = Callable[..., dict[str, Any]]


class IncidentService:
    def __init__(
        self,
        settings: Settings,
        *,
        repo: Repo | None = None,
        dispatcher: Dispatcher | None = None,
        provider: TriageProvider | None = None,
    ) -> None:
        self.settings = settings
        self._storage = open_storage(settings)
        self.repo: Repo = repo or self._storage.repo
        if dispatcher is not None:
            self._dispatcher = dispatcher
        elif settings.action_provider == "zapier":
            self._dispatcher = request_zapier_actions
        else:
            self._dispatcher = request_n8n_actions
        self.events = build_event_bus(settings)
        self._graph = build_incident_graph(
            settings, self._storage.checkpointer, provider or build_provider(settings)
        )

    def close(self) -> None:
        self.events.close()
        self._storage.close()

    # -- queries ---------------------------------------------------------------------------
    def get(self, run_id: str) -> dict[str, Any]:
        state = self.repo.get(run_id)
        if state is None:
            raise IncidentNotFoundError(run_id)
        return state

    def search(
        self,
        *,
        statuses: list[str] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        return self.repo.search(statuses=statuses, query=query, limit=limit, offset=offset)

    def audit(self, run_id: str) -> list[dict[str, Any]]:
        return self.repo.audit_for(run_id)

    # -- commands --------------------------------------------------------------------------
    def start(self, payload: dict[str, Any], *, actor: str = "api") -> dict[str, Any]:
        """Create a run. Re-submitting the same run id returns the stored run unchanged."""
        clean: dict[str, Any] = redact(payload)
        run_id = clean.get("run_id") or stable_id(clean)
        clean["run_id"] = run_id
        existing = self.repo.get(run_id)
        if existing is not None:
            log.info("incident.duplicate", run_id=run_id)
            return existing

        clean["max_retries"] = self.settings.max_research_attempts
        clean["confidence_threshold"] = self.settings.confidence_threshold
        with bound_context(run_id=run_id), span("incident.run", run_id=run_id):
            self._graph.invoke(clean, config=self._config(run_id))
            state = self._sync(run_id)
            self.repo.add_audit(run_id, "created", actor, {"status": state["status"]})
            log.info("incident.created", status=state["status"], severity=state.get("severity"))
        return state

    def decide(
        self,
        run_id: str,
        idempotency_key: str,
        decision: str,
        *,
        approved_actions: list[str] | None = None,
        actor: str = "api",
    ) -> dict[str, Any]:
        """Approve or reject. ``approved_actions`` narrows an approval to a subset of actions."""
        state = self.get(run_id)
        if idempotency_key != state.get("idempotency_key"):
            raise IncidentConflictError("idempotency key mismatch")
        proposed = state.get("proposed_actions", [])
        chosen = proposed if approved_actions is None else approved_actions
        if decision == "rejected":
            chosen = []
        elif not set(chosen) <= set(proposed):
            raise IncidentConflictError("approved_actions must be a subset of proposed_actions")
        elif not chosen:
            raise IncidentConflictError("approve at least one action, or reject the incident")
        if not self.repo.claim(run_id, "awaiting_approval"):
            raise IncidentConflictError(
                f"run is not awaiting approval (status: {self.get(run_id)['status']})"
            )
        with bound_context(run_id=run_id):
            try:
                self._graph.invoke(
                    Command(resume={"decision": decision, "approved_actions": chosen}),
                    config=self._config(run_id),
                )
            except Exception:
                self.repo.release(run_id, "awaiting_approval")
                raise
            state = self._sync(run_id)
            self.repo.add_audit(run_id, f"decision:{decision}", actor, {"approved_actions": chosen})
            log.info("incident.decided", decision=decision)
            if decision == "approved":
                state = self._dispatch(run_id, state, actor)
        return state

    def retry_dispatch(self, run_id: str, *, actor: str = "api") -> dict[str, Any]:
        state = self.get(run_id)
        if state.get("status") != "failed" or state.get("approval") != "approved":
            raise IncidentConflictError("only an approved run with a failed dispatch can retry")
        if not self.repo.claim(run_id, "failed"):
            raise IncidentConflictError("dispatch retry already in progress")
        with bound_context(run_id=run_id):
            return self._dispatch(run_id, state, actor)

    # -- internals -------------------------------------------------------------------------
    @staticmethod
    def _config(run_id: str) -> Any:
        return {"configurable": {"thread_id": run_id}}

    def _sync(self, run_id: str) -> dict[str, Any]:
        values = dict(self._graph.get_state(self._config(run_id)).values)
        self.repo.upsert(values)
        state = self.get(run_id)
        self.events.publish({"type": "incident", "run_id": run_id, "status": state.get("status")})
        return state

    def _dispatch(self, run_id: str, state: dict[str, Any], actor: str) -> dict[str, Any]:
        if self.settings.action_provider == "zapier":
            url = self.settings.zapier_action_webhook_url
            attempts = self.settings.zapier_dispatch_attempts
            backoff = self.settings.zapier_dispatch_backoff
            token = self.settings.zapier_action_token
        else:
            url = self.settings.n8n_action_webhook_url
            attempts = self.settings.n8n_dispatch_attempts
            backoff = self.settings.n8n_dispatch_backoff
            token = self.settings.n8n_action_token
        options: dict[str, Any] = {"attempts": attempts, "backoff": backoff, "token": token}
        if self.settings.webhook_signing_secret:
            options["signing_secret"] = self.settings.webhook_signing_secret
        update: dict[str, Any]
        if not url:
            update = {"action_dispatch": "not_configured", "status": "succeeded"}
            self.repo.add_audit(run_id, "dispatch:not_configured", actor)
        else:
            try:
                if not host_allowed(url, self.settings.outbound_allowed_hosts):
                    raise ValueError("action webhook host is not in OUTBOUND_ALLOWED_HOSTS")
                with span(
                    "action.dispatch",
                    run_id=run_id,
                    provider=self.settings.action_provider,
                ):
                    self._dispatcher(url, dict(state), **options)
            except (httpx.HTTPError, ValueError) as error:
                log.error("incident.dispatch_failed", error=str(error))
                update = {
                    "action_dispatch": "failed",
                    "status": "failed",
                    "timeline": [f"action dispatch failed: {type(error).__name__}"],
                }
                self.repo.add_audit(run_id, "dispatch:failed", actor, {"error": str(error)})
            else:
                update = {
                    "action_dispatch": "sent",
                    "status": "succeeded",
                    "timeline": ["actions dispatched to n8n"],
                }
                self.repo.add_audit(run_id, "dispatch:sent", actor)
        self._graph.update_state(self._config(run_id), update, as_node="finalize")
        return self._sync(run_id)
