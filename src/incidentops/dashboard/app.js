const state = {
  incidents: [],
  selected: null,
  filter: "open",
};

const list = document.querySelector("#incident-list");
const detail = document.querySelector("#detail");
const count = document.querySelector("#incident-count");
const syncLabel = document.querySelector("#sync-label");
const apiKey = document.querySelector("#api-key");
const toast = document.querySelector("#toast");

apiKey.value = sessionStorage.getItem("incidentops-api-key") || "";
apiKey.addEventListener("change", () => {
  sessionStorage.setItem("incidentops-api-key", apiKey.value);
  loadIncidents();
});

document.querySelectorAll(".filter").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelector(".filter.active")?.classList.remove("active");
    button.classList.add("active");
    state.filter = button.dataset.filter;
    renderQueue();
  });
});

function headers() {
  const result = { "Content-Type": "application/json" };
  if (apiKey.value) result["X-API-Key"] = apiKey.value;
  return result;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function humanize(value) {
  return String(value || "unknown").replaceAll("_", " ");
}

function isOpen(incident) {
  return ["running", "awaiting_approval", "needs_human"].includes(incident.status);
}

function filteredIncidents() {
  if (state.filter === "all") return state.incidents;
  if (state.filter === "open") return state.incidents.filter(isOpen);
  return state.incidents.filter((incident) => incident.status === state.filter);
}

async function loadIncidents({ quiet = false } = {}) {
  if (!quiet) list.innerHTML = '<p class="loading">Reading incident checkpoints…</p>';
  try {
    const response = await fetch("/incidents", { headers: headers() });
    if (!response.ok) throw new Error(response.status === 401 ? "API key required" : "Incident API unavailable");
    state.incidents = await response.json();
    syncLabel.textContent = `Synced ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    if (state.selected) {
      state.selected = state.incidents.find((item) => item.run_id === state.selected.run_id) || null;
    }
    if (!state.selected && state.incidents.length) state.selected = state.incidents[0];
    renderQueue();
    renderDetail();
  } catch (error) {
    syncLabel.textContent = "Connection unavailable";
    list.innerHTML = `<p class="queue-empty">${escapeHtml(error.message)}. Check the key and API process.</p>`;
    count.textContent = "—";
  }
}

function renderQueue() {
  const incidents = filteredIncidents();
  count.textContent = incidents.length;
  if (!incidents.length) {
    list.innerHTML = '<p class="queue-empty">No incidents match this view.</p>';
    return;
  }
  list.innerHTML = incidents.map((incident) => `
    <button class="incident-row severity-${escapeHtml((incident.severity || "none").toLowerCase())}
      ${state.selected?.run_id === incident.run_id ? "selected" : ""}"
      data-run-id="${escapeHtml(incident.run_id)}">
      <span class="pulse-rail" aria-hidden="true"></span>
      <span>
        <span class="incident-meta">
          <span>${escapeHtml(incident.severity || "unrated")}</span>
          <span>${escapeHtml(humanize(incident.status))}</span>
        </span>
        <strong>${escapeHtml(incident.summary || "Untitled incident")}</strong>
        <small>${escapeHtml(incident.service || "Service unknown")} · ${escapeHtml(incident.run_id)}</small>
      </span>
    </button>
  `).join("");
  list.querySelectorAll(".incident-row").forEach((row) => {
    row.addEventListener("click", () => {
      state.selected = state.incidents.find((item) => item.run_id === row.dataset.runId);
      renderQueue();
      renderDetail();
    });
  });
}

function renderDetail() {
  const incident = state.selected;
  if (!incident) return;
  const canDecide = incident.status === "awaiting_approval";
  detail.innerHTML = `
    <header class="detail-head">
      <div>
        <p class="eyebrow">${escapeHtml(incident.run_id)} · ${escapeHtml(humanize(incident.status))}</p>
        <h2>${escapeHtml(incident.summary)}</h2>
      </div>
      <span class="severity-stamp">${escapeHtml(incident.severity || "UNRATED")}</span>
    </header>

    <div class="decision-bar">
      <p>${canDecide ? "Human decision required before external actions." : `Run is ${escapeHtml(humanize(incident.status))}.`}</p>
      <button class="action reject" data-decision="rejected" ${canDecide ? "" : "disabled"}>Reject proposal</button>
      <button class="action approve" data-decision="approved" ${canDecide ? "" : "disabled"}>Approve proposal</button>
    </div>

    <dl class="facts">
      ${fact("Service", incident.service || "Unknown")}
      ${fact("Owner", incident.owner || "Unassigned")}
      ${fact("Confidence", `${Math.round((incident.confidence || 0) * 100)}%`)}
      ${fact("Evidence", (incident.evidence || []).length)}
    </dl>

    <div class="detail-grid">
      <div>
        ${section("Proposed actions", `<ol class="action-list">${(incident.proposed_actions || []).map((action) => `<li>${escapeHtml(action)}</li>`).join("") || "<li>No actions proposed.</li>"}</ol>`)}
        ${section("Investigation evidence", renderEvidence(incident.evidence || []))}
        ${section("Runbook guidance", renderRunbooks(incident.runbook_hits || []))}
      </div>
      <div>
        ${section("Response log", `<ol class="timeline">${(incident.timeline || []).map((event) => `<li>${escapeHtml(event)}</li>`).join("")}</ol>`)}
        ${incident.missing_fields?.length ? section("Missing context", `<p>${incident.missing_fields.map(humanize).map(escapeHtml).join(", ")}</p>`) : ""}
        ${incident.conflicts?.length ? section("Conflicts", `<p>${incident.conflicts.map(escapeHtml).join("<br>")}</p>`) : ""}
      </div>
    </div>
  `;
  detail.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", () => decide(button.dataset.decision));
  });
}

function fact(label, value) {
  return `<div class="fact"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
}

function section(title, body) {
  return `<section class="section"><h3>${escapeHtml(title)}</h3>${body}</section>`;
}

function renderEvidence(evidence) {
  if (!evidence.length) return "<p>No tool evidence attached.</p>";
  return evidence.map((item) => `
    <article class="evidence-item">
      <b>${escapeHtml(humanize(item.source))}</b>
      <pre>${escapeHtml(JSON.stringify(item.data, null, 2))}</pre>
    </article>
  `).join("");
}

function renderRunbooks(hits) {
  if (!hits.length) return "<p>No matching runbook section found.</p>";
  return hits.map((hit) => `
    <article class="runbook">
      <b>${escapeHtml(hit.title)}</b>
      <p>${escapeHtml(hit.excerpt)}</p>
      <code>${escapeHtml(hit.path)}</code>
    </article>
  `).join("");
}

async function decide(decision) {
  const incident = state.selected;
  detail.querySelectorAll("[data-decision]").forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(`/incidents/${encodeURIComponent(incident.run_id)}/decision`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ idempotency_key: incident.idempotency_key, decision }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Decision failed");
    const result = await response.json();
    const message = decision === "rejected"
      ? "Proposal rejected."
      : result.action_dispatch === "sent"
        ? "Approved and sent to n8n."
        : "Approved. Configure the n8n action webhook to execute.";
    showToast(message);
    await loadIncidents({ quiet: true });
  } catch (error) {
    showToast(error.message);
    renderDetail();
  }
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2600);
}

loadIncidents();
window.setInterval(() => loadIncidents({ quiet: true }), 15000);
