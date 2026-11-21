"""Application service: runs, resumes and dispatches incidents.

The compiled LangGraph (with a SQLite checkpointer) owns workflow state. This service is the
only place that talks to the graph, keeps the queryable read-model in sync, writes the audit
log, and dispatches approved actions to n8n.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx
from langgraph.types import Command

from incidentops.agents.factory import build_provider
from incidentops.agents.resilient import TriageProvider
from incidentops.api.events import build_event_bus
from incidentops.core.config import Settings
from incidentops.core.errors import (
    AccessDeniedError,
    IncidentConflictError,
    IncidentNotFoundError,
)
from incidentops.core.logging import bound_context, get_logger
from incidentops.core.metrics import Metrics
from incidentops.core.redact import redact
from incidentops.core.signing import host_allowed
from incidentops.core.telemetry import span
from incidentops.decision import JevAdvisor, JevClient
from incidentops.persistence.audit import ChainReport
from incidentops.persistence.base import Repo
from incidentops.persistence.factory import open_storage
from incidentops.persistence.jobs import Job
from incidentops.security.principal import Principal
from incidentops.tools.n8n import request_actions as request_n8n_actions
from incidentops.tools.zapier import request_actions as request_zapier_actions
from incidentops.workflows.incident.graph import build_incident_graph
from incidentops.workflows.incident.nodes import stable_id

log = get_logger(__name__)

Dispatcher = Callable[..., dict[str, Any]]

RUN_JOB = "run"
# Statuses in which the analysis part of a run is finished (it now waits on a person or is over).
SETTLED = frozenset({"awaiting_approval", "needs_human", "succeeded", "cancelled", "failed"})
# A run in one of these has not finished its analysis: a caller waiting on it keeps waiting.
IN_PROGRESS = frozenset({"queued", "running"})


class IncidentService:
    def __init__(
        self,
        settings: Settings,
        *,
        repo: Repo | None = None,
        dispatcher: Dispatcher | None = None,
        provider: TriageProvider | None = None,
        metrics: Metrics | None = None,
        jev: JevClient | None = None,
    ) -> None:
        self.settings = settings
        self.metrics = metrics or Metrics(False)
        self._storage = open_storage(settings)
        self.repo: Repo = repo or self._storage.repo
        if dispatcher is not None:
            self._dispatcher = dispatcher
        elif settings.action_provider == "zapier":
            self._dispatcher = request_zapier_actions
        else:
            self._dispatcher = request_n8n_actions
        self.events = build_event_bus(settings)
        self.jobs = self._storage.jobs
        # Set by whoever runs workers in this process, to start a queued job without waiting
        # for the next poll.
        self.on_enqueue: Callable[[], None] | None = None
        # One client for the workflow advisor and POST /decisions, so usage is counted together.
        self.jev: JevClient | None = jev or (JevClient(settings) if settings.jev_enabled else None)
        self._graph = build_incident_graph(
            settings,
            self._storage.checkpointer,
            provider or build_provider(settings),
            advisor=JevAdvisor(self.jev, settings) if self.jev else None,
        )

    def _invoke(self, graph_input: Any, config: Any) -> Any:
        """Run the graph, persisting checkpoints as ``CHECKPOINT_DURABILITY`` says."""
        return self._graph.invoke(
            graph_input, config=config, durability=self.settings.checkpoint_durability
        )

    def close(self) -> None:
        if self.jev is not None:
            self.jev.close()
        self.events.close()
        self._storage.close()

    # -- queries ---------------------------------------------------------------------------
    def get(self, run_id: str, tenant: str | None = None) -> dict[str, Any]:
        """The run, or IncidentNotFoundError. With ``tenant``, another tenant's run is not found."""
        state = self.repo.get(run_id, tenant=tenant)
        if state is None:
            raise IncidentNotFoundError(run_id)
        if state.get("status") == "awaiting_approval":
            needed = self._approvals_required(state)
            if needed > 1:
                state["approval_progress"] = {
                    "required": needed,
                    "approved_by": [a["actor"] for a in self.repo.approvals_for(run_id)],
                }
        return state

    def search(
        self,
        *,
        statuses: list[str] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        tenant: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return self.repo.search(
            statuses=statuses, query=query, limit=limit, offset=offset, tenant=tenant
        )

    def audit(self, run_id: str, tenant: str | None = None) -> list[dict[str, Any]]:
        return self.repo.audit_for(run_id, tenant=tenant)

    def audit_search(self, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        return self.repo.audit_search(**filters)

    def audit_verify(self, tenant: str) -> ChainReport:
        return self.repo.audit_verify(tenant)

    def _tenant(self, tenant: str | None) -> str:
        return tenant or self.settings.default_tenant

    def _new_run_id(self, clean: dict[str, Any], tenant: str) -> str:
        """The caller's run id, else a hash of the report. Other tenants' ids are salted with
        the tenant so identical reports from two tenants never collide."""
        explicit = clean.get("run_id")
        if explicit:
            return str(explicit)
        if tenant == self.settings.default_tenant:
            return stable_id(clean)
        return stable_id({**clean, "tenant_id": tenant})

    def _existing(self, run_id: str, tenant: str) -> dict[str, Any] | None:
        """The stored run if it is this tenant's. A run id owned by someone else is a conflict
        (and nothing about that run is revealed)."""
        owner = self.repo.tenant_of(run_id)
        if owner is None:
            return None
        if owner != tenant:
            raise IncidentConflictError("run_id is already in use")
        return self.get(run_id)

    def _approvals_required(self, state: dict[str, Any]) -> int:
        settings = self.settings
        if settings.four_eyes_approvals > 1 and state.get("severity") in (
            settings.four_eyes_severities
        ):
            return settings.four_eyes_approvals
        return 1

    # -- commands --------------------------------------------------------------------------
    def start(
        self, payload: dict[str, Any], *, actor: str = "api", tenant: str | None = None
    ) -> dict[str, Any]:
        """Create a run. Re-submitting the same run id returns the stored run unchanged."""
        tenant = self._tenant(tenant)
        clean: dict[str, Any] = redact(payload)
        run_id = self._new_run_id(clean, tenant)
        clean["run_id"] = run_id
        existing = self._existing(run_id, tenant)
        if existing is not None:
            log.info("incident.duplicate", run_id=run_id)
            return existing

        clean["max_retries"] = self.settings.max_research_attempts
        clean["confidence_threshold"] = self.settings.confidence_threshold
        with bound_context(run_id=run_id), span("incident.run", run_id=run_id):
            self._invoke(clean, self._config(run_id))
            state = self._sync(run_id, tenant)
            self.repo.add_audit(run_id, "created", actor, {"status": state["status"]})
            log.info("incident.created", status=state["status"], severity=state.get("severity"))
            self.metrics.incident_created(state.get("status"), state.get("severity"))
        return state

    # -- asynchronous execution ------------------------------------------------------------
    def create(
        self, payload: dict[str, Any], *, actor: str = "api", tenant: str | None = None
    ) -> dict[str, Any]:
        """Start a run the way EXECUTION_MODE says: inline, or queued for a worker."""
        if self.settings.execution_mode == "async":
            return self.submit(payload, actor=actor, tenant=tenant)
        return self.start(payload, actor=actor, tenant=tenant)

    def submit(
        self, payload: dict[str, Any], *, actor: str = "api", tenant: str | None = None
    ) -> dict[str, Any]:
        """Queue a run for a worker and return at once, with the run in status ``queued``.

        Re-submitting a known run id returns the stored run. The run row is created first and
        only if absent, so two concurrent submits cannot reset a run a worker already moved on.
        """
        tenant = self._tenant(tenant)
        clean: dict[str, Any] = redact(payload)
        run_id = self._new_run_id(clean, tenant)
        clean["run_id"] = run_id
        existing = self._existing(run_id, tenant)
        if existing is not None:
            log.info("incident.duplicate", run_id=run_id)
            return existing

        clean["max_retries"] = self.settings.max_research_attempts
        clean["confidence_threshold"] = self.settings.confidence_threshold
        queued = {
            **clean,
            "idempotency_key": run_id,
            "status": "queued",
            "retries": 0,
            "timeline": ["queued for analysis"],
        }
        if not self.repo.insert_new(queued, tenant):
            return self._existing(run_id, tenant) or self.get(run_id)
        self.jobs.enqueue(
            RUN_JOB,
            run_id,
            {"incident": clean, "actor": actor, "tenant": tenant},
            max_attempts=self.settings.job_max_attempts,
        )
        self.repo.add_audit(run_id, "queued", actor, {})
        self._announce(run_id, "queued")
        if self.on_enqueue is not None:
            self.on_enqueue()
        log.info("incident.queued", run_id=run_id)
        return self.get(run_id)

    def wait_for(self, run_id: str, seconds: float, tenant: str | None = None) -> dict[str, Any]:
        """Wait up to ``seconds`` for a queued run to finish its analysis, then return it."""
        deadline = time.monotonic() + max(0.0, seconds)
        state = self.get(run_id, tenant)
        while state.get("status") in IN_PROGRESS and time.monotonic() < deadline:
            time.sleep(0.1)
            state = self.get(run_id, tenant)
        return state

    def handle_job(self, job: Job) -> None:
        """Run one queued analysis. Raises on failure so the worker retries it with backoff."""
        if job.kind != RUN_JOB:
            raise ValueError(f"unknown job kind {job.kind!r}")
        clean: dict[str, Any] = job.payload["incident"]
        actor: str = job.payload.get("actor", "api")
        run_id: str = clean["run_id"]
        config = self._config(run_id)
        if self.repo.claim(
            run_id, "queued", to="running", visible=True
        ):  # a no-op when this is a retry
            self._announce(run_id, "running")
        with (
            bound_context(run_id=run_id, attempt=job.attempts),
            span("incident.run", run_id=run_id),
        ):
            snapshot = self._graph.get_state(config)
            if not snapshot.values:
                self._invoke(clean, config)
            elif snapshot.values.get("status") not in SETTLED:
                # An earlier attempt died midway: continue from its last checkpoint.
                self._invoke(None, config)
            state = self._sync(run_id)
            if state.get("status") not in SETTLED:
                raise RuntimeError(f"analysis ended in unexpected status {state.get('status')!r}")
            if not any(entry["event"] == "created" for entry in self.repo.audit_for(run_id)):
                self.repo.add_audit(run_id, "created", actor, {"status": state["status"]})
            log.info("incident.created", status=state["status"], severity=state.get("severity"))
            self.metrics.incident_created(state.get("status"), state.get("severity"))

    def job_failed(self, job: Job, error: str, outcome: str) -> None:
        """Worker callback after a failed attempt. ``outcome`` is 'queued' (retry) or 'dead'."""
        if outcome == "queued":
            self.repo.claim(job.run_id, "running", to="queued", visible=True)
            self.repo.add_audit(
                job.run_id, "run:retry", "worker", {"attempt": job.attempts, "error": error[:500]}
            )
            self._announce(job.run_id, "queued")
        else:
            self._mark_errored(job.run_id, error, attempts=job.attempts)

    def job_reaped(self, kind: str, run_id: str) -> None:
        """Worker callback for a job whose worker vanished on its final attempt."""
        if kind == RUN_JOB:
            self._mark_errored(run_id, "the worker stopped responding on the final attempt")

    def retry_run(
        self, run_id: str, *, actor: str = "api", tenant: str | None = None
    ) -> dict[str, Any]:
        """Give a run that failed analysis (status ``errored``) a fresh set of attempts."""
        state = self.get(run_id, tenant)
        if state.get("status") != "errored":
            raise IncidentConflictError("only a run that failed analysis can be retried")
        if not self.jobs.requeue_dead(RUN_JOB, run_id):
            raise IncidentConflictError("there is no failed job to retry for this run")
        self.repo.claim(run_id, "errored", to="queued", visible=True)
        self.repo.add_audit(run_id, "run:requeued", actor)
        self._announce(run_id, "queued")
        if self.on_enqueue is not None:
            self.on_enqueue()
        return self.get(run_id)

    def queue_stats(self, tenant: str | None = None) -> dict[str, Any]:
        """Queue counts (aggregates, safe to share) and recent dead jobs of the tenant's runs."""
        dead = self.jobs.dead(20)
        if tenant is not None:
            dead = [job for job in dead if self.repo.tenant_of(job["run_id"]) == tenant]
        return {**self.jobs.stats(), "dead_jobs": dead}

    def _mark_errored(self, run_id: str, error: str, **detail: Any) -> None:
        stored = self.repo.get(run_id) or {"run_id": run_id}
        state = {
            k: v for k, v in stored.items() if k not in ("created_at", "updated_at", "tenant_id")
        }
        state["status"] = "errored"
        state["error"] = error[:500]
        state["timeline"] = [*state.get("timeline", []), f"analysis failed: {error[:200]}"]
        self.repo.upsert(state)
        self.repo.add_audit(run_id, "run:errored", "worker", {"error": error[:500], **detail})
        self._announce(run_id, "errored")
        log.error("incident.errored", run_id=run_id, error=error[:200])

    def _announce(self, run_id: str, status: str) -> None:
        self.events.publish(
            {
                "type": "incident",
                "run_id": run_id,
                "status": status,
                "tenant": self.repo.tenant_of(run_id),
            }
        )

    def decide(
        self,
        run_id: str,
        idempotency_key: str,
        decision: str,
        *,
        approved_actions: list[str] | None = None,
        actor: str = "api",
        principal: Principal | None = None,
    ) -> dict[str, Any]:
        """Approve or reject. ``approved_actions`` narrows an approval to a subset of actions.

        With a ``principal`` the actor and tenant come from the verified identity, another
        tenant's run is not found, and the separation-of-duties rules apply: nobody approves
        their own incident (``FORBID_SELF_APPROVAL``), and severities in ``FOUR_EYES_SEVERITIES``
        need ``FOUR_EYES_APPROVALS`` different approvers. One rejection is enough to reject.
        """
        tenant = principal.tenant if principal else None
        actor = principal.actor if principal else actor
        state = self.get(run_id, tenant)
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
        approvers: list[str] = []
        if decision == "approved":
            self._check_separation(run_id, principal)
            needed = self._approvals_required(state)
            if needed > 1:
                approvers = self._record_approval(run_id, principal, chosen, needed)
                if len(approvers) < needed:
                    return self.get(run_id)  # recorded; still waiting for another approver
        if not self.repo.claim(run_id, "awaiting_approval"):
            raise IncidentConflictError(
                f"run is not awaiting approval (status: {self.get(run_id)['status']})"
            )
        with bound_context(run_id=run_id):
            try:
                self._invoke(
                    Command(resume={"decision": decision, "approved_actions": chosen}),
                    self._config(run_id),
                )
            except Exception:
                self.repo.release(run_id, "awaiting_approval")
                raise
            state = self._sync(run_id)
            detail: dict[str, Any] = {"approved_actions": chosen}
            if approvers:
                detail["approvers"] = approvers
            self.repo.add_audit(run_id, f"decision:{decision}", actor, detail)
            log.info("incident.decided", decision=decision)
            self.metrics.decision(decision)
            if decision == "approved":
                state = self._dispatch(run_id, state, actor)
        return state

    def _check_separation(self, run_id: str, principal: Principal | None) -> None:
        """Whoever opened an incident may not approve it, when FORBID_SELF_APPROVAL is on."""
        if principal is None or not principal.named or not self.settings.forbid_self_approval:
            return
        opened = next(
            (e for e in self.repo.audit_for(run_id) if e["event"] in ("queued", "created")), None
        )
        if opened is not None and opened["actor"] == principal.actor:
            raise AccessDeniedError("the person who opened an incident cannot also approve it")

    def _record_approval(
        self, run_id: str, principal: Principal | None, chosen: list[str], needed: int
    ) -> list[str]:
        if principal is None or not principal.named:
            raise AccessDeniedError(
                f"this incident needs {needed} approvers; approve with a named identity "
                "(OIDC login or a scoped API key), not the shared key"
            )
        for earlier in self.repo.approvals_for(run_id):
            if sorted(earlier["actions"]) != sorted(chosen):
                raise IncidentConflictError(
                    "approvers must approve the same actions; an earlier approver chose "
                    + ", ".join(earlier["actions"])
                )
        approvers = self.repo.add_approval(run_id, principal.subject, chosen, principal.actor)
        self.repo.add_audit(
            run_id,
            "approval:recorded",
            principal.actor,
            {"approved_actions": chosen, "count": len(approvers), "required": needed},
        )
        return approvers

    def retry_dispatch(
        self, run_id: str, *, actor: str = "api", tenant: str | None = None
    ) -> dict[str, Any]:
        state = self.get(run_id, tenant)
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

    def _sync(self, run_id: str, tenant: str | None = None) -> dict[str, Any]:
        values = dict(self._graph.get_state(self._config(run_id)).values)
        self.repo.upsert(values, self._tenant(tenant))  # the tenant only applies to a new row
        state = self.get(run_id)
        self.events.publish(
            {
                "type": "incident",
                "run_id": run_id,
                "status": state.get("status"),
                "tenant": state.get("tenant_id"),
            }
        )
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
            self.metrics.dispatch("not_configured")
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
                self.metrics.dispatch("failed")
            else:
                update = {
                    "action_dispatch": "sent",
                    "status": "succeeded",
                    "timeline": ["actions dispatched to n8n"],
                }
                self.repo.add_audit(run_id, "dispatch:sent", actor)
                self.metrics.dispatch("sent")
        self._graph.update_state(self._config(run_id), update, as_node="finalize")
        return self._sync(run_id)
