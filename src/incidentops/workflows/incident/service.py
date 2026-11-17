"""Application service: runs, resumes and dispatches incidents.

The compiled LangGraph (with a SQLite checkpointer) owns workflow state. This service is the
only place that talks to the graph, keeps the queryable read-model in sync, writes the audit
log, and dispatches approved actions to n8n.
"""

import sqlite3
from collections.abc import Callable
from typing import Any

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from incidentops.agents.factory import build_provider
from incidentops.agents.resilient import TriageProvider
from incidentops.api.events import EventBus
from incidentops.core.config import Settings
from incidentops.core.errors import IncidentConflictError, IncidentNotFoundError
from incidentops.core.logging import bound_context, get_logger
from incidentops.core.redact import redact
from incidentops.core.telemetry import span
from incidentops.persistence.repo import IncidentRepo
from incidentops.tools.n8n import request_actions
from incidentops.workflows.incident.graph import build_incident_graph
from incidentops.workflows.incident.nodes import stable_id

log = get_logger(__name__)

Dispatcher = Callable[..., dict[str, Any]]


class IncidentService:
    def __init__(
        self,
        settings: Settings,
        *,
        repo: IncidentRepo | None = None,
        dispatcher: Dispatcher = request_actions,
        provider: TriageProvider | None = None,
    ) -> None:
        self.settings = settings
        self.repo = repo or IncidentRepo(settings.database_path)
        self._dispatcher = dispatcher
        self.events = EventBus()
        self._connection = sqlite3.connect(settings.database_path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._graph = build_incident_graph(
            settings, SqliteSaver(self._connection), provider or build_provider(settings)
        )

    def close(self) -> None:
        self._connection.close()

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
        url = self.settings.n8n_action_webhook_url
        update: dict[str, Any]
        if not url:
            update = {"action_dispatch": "not_configured", "status": "succeeded"}
            self.repo.add_audit(run_id, "dispatch:not_configured", actor)
        else:
            try:
                with span("n8n.dispatch", run_id=run_id):
                    self._dispatcher(
                        url,
                        dict(state),
                        attempts=self.settings.n8n_dispatch_attempts,
                        backoff=self.settings.n8n_dispatch_backoff,
                    )
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
