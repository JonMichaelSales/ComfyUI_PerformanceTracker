import { app } from "../../scripts/app.js";

const API_ROOT = "/performance-tracker";

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child === undefined || child === null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function formatDuration(ms) {
  if (ms === null || ms === undefined) return "-";
  const seconds = Number(ms) / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const mins = Math.floor(seconds / 60);
  const rem = Math.round(seconds % 60);
  return `${mins}m ${rem}s`;
}

function formatDate(ts) {
  if (!ts) return "-";
  return new Date(Number(ts)).toLocaleString();
}

function shortHash(hash) {
  return hash ? String(hash).slice(0, 10) : "-";
}

function formatRunCount(row) {
  const included = Number(row.run_count) || 0;
  const excluded = Number(row.excluded_count) || 0;
  return excluded ? `${included} (+${excluded} excluded)` : String(included);
}

async function api(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload?.error?.message || message;
    } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

class PerformanceTrackerPanel {
  constructor() {
    this.activeTab = "models";
    this.loaded = false;
    this.limit = 50;
    this.root = el("section", { class: "pt-panel", "aria-label": "Performance Tracker" });
    this.button = el("button", { class: "pt-rail-button", text: "Perf", title: "Performance Tracker", onclick: () => this.toggle() });
    this.build();
  }

  mount() {
    document.body.append(this.button, this.root);
  }

  build() {
    this.root.append(
      el("header", { class: "pt-header" }, [
        el("div", {}, [
          el("h2", { text: "Performance" }),
          el("p", { text: "Completed generation history and derived model averages" }),
        ]),
        el("div", { class: "pt-actions" }, [
          el("button", { text: "Refresh", onclick: () => this.refresh() }),
          el("button", { text: "Clear", class: "pt-danger", onclick: () => this.clearHistory() }),
          el("button", { text: "x", title: "Close", onclick: () => this.close() }),
        ]),
      ]),
    );

    this.overview = el("div", { class: "pt-overview" });
    this.tabs = el("nav", { class: "pt-tabs" });
    for (const [id, label] of [["models", "Models"], ["recent", "Recent Runs"], ["workflows", "Workflows"], ["loras", "LoRAs"]]) {
      this.tabs.append(el("button", { text: label, "data-tab": id, onclick: () => this.setTab(id) }));
    }
    this.content = el("div", { class: "pt-content" });
    this.status = el("div", { class: "pt-status" });
    this.root.append(this.overview, this.tabs, this.content, this.status);
  }

  async toggle() {
    if (this.root.classList.contains("is-open")) {
      this.close();
      return;
    }
    this.root.classList.add("is-open");
    if (!this.loaded) await this.refresh();
  }

  close() {
    this.root.classList.remove("is-open");
  }

  async setTab(tab) {
    this.activeTab = tab;
    this.updateTabButtons();
    await this.refresh();
  }

  updateTabButtons() {
    this.tabs.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.tab === this.activeTab);
    });
  }

  async refresh() {
    this.loaded = true;
    this.updateTabButtons();
    this.setStatus("Loading...");
    try {
      const [overview, tabPayload] = await Promise.all([api("/stats/overview"), this.loadTab()]);
      this.renderOverview(overview);
      this.renderTab(tabPayload);
      this.setStatus("");
    } catch (error) {
      this.setStatus(error.message || String(error), true);
    }
  }

  loadTab() {
    if (this.activeTab === "models") return api(`/stats/models?limit=${this.limit}`);
    if (this.activeTab === "recent") return api(`/runs?limit=${this.limit}`);
    if (this.activeTab === "workflows") return api(`/stats/workflows?limit=${this.limit}`);
    return api(`/stats/loras?limit=${this.limit}`);
  }

  renderOverview(data) {
    this.overview.replaceChildren(
      this.metric("Runs", data.total_runs ?? 0),
      this.metric("Average", formatDuration(data.avg_duration_ms)),
      this.metric("Cache Rate", `${Math.round((Number(data.avg_cache_rate) || 0) * 100)}%`),
      this.metric("Fastest", formatDuration(data.fastest_ms)),
      this.metric("Slowest", formatDuration(data.slowest_ms)),
    );
  }

  metric(label, value) {
    return el("div", { class: "pt-metric" }, [el("span", { text: label }), el("strong", { text: String(value) })]);
  }

  renderTab(payload) {
    if (this.activeTab === "models") this.renderModels(payload.models || []);
    else if (this.activeTab === "recent") this.renderRuns(payload.runs || []);
    else if (this.activeTab === "workflows") this.renderWorkflows(payload.workflows || []);
    else this.renderLoras(payload.loras || []);
  }

  renderModels(rows) {
    this.renderTable(["Model", "Runs", "Average", "Fastest", "Slowest", "Avg Steps", "Avg MP"], rows, (row) => [
      row.model,
      formatRunCount(row),
      formatDuration(row.avg_duration_ms),
      formatDuration(row.fastest_ms),
      formatDuration(row.slowest_ms),
      row.avg_steps ? Number(row.avg_steps).toFixed(1) : "-",
      row.avg_pixels ? (Number(row.avg_pixels) / 1_000_000).toFixed(2) : "-",
    ], (row) => this.openRunGroup({ type: "model", value: row.model, label: row.model }));
  }

  renderRuns(rows) {
    const table = this.makeTable(["When", "Duration", "Model", "Sampler", "Steps", "Resolution", "Nodes", "Status", "Avg"]);
    for (const row of rows) {
      const tr = el("tr", { class: row.excluded_from_stats ? "is-excluded" : "", onclick: () => this.openRun(row.prompt_id), title: "Open run detail" });
      tr.append(
        ...[
          formatDate(row.end_ts || row.start_ts),
          formatDuration(row.duration_ms),
          row.primary_model || "-",
          row.primary_sampler || "-",
          row.primary_steps ?? "-",
          row.primary_width && row.primary_height ? `${row.primary_width}x${row.primary_height} x${row.primary_batch_size || 1}` : "-",
          `${row.cached_node_count}/${row.executed_node_count}/${row.total_node_count}`,
          row.status || "-",
          row.excluded_from_stats ? "Excluded" : "Included",
        ].map((value) => el("td", { text: String(value) })),
      );
      table.querySelector("tbody").append(tr);
    }
    this.content.replaceChildren(rows.length ? table : this.empty("No completed runs recorded yet."));
  }

  renderWorkflows(rows) {
    this.renderTable(["Workflow", "Runs", "Average", "Slowest", "Sample Model"], rows, (row) => [
      shortHash(row.workflow_hash),
      formatRunCount(row),
      formatDuration(row.avg_duration_ms),
      formatDuration(row.slowest_ms),
      row.sample_model || "-",
    ], (row) => this.openRunGroup({ type: "workflow_hash", value: row.workflow_hash, label: shortHash(row.workflow_hash) }));
  }

  renderLoras(rows) {
    this.renderTable(["LoRA", "Runs", "Average"], rows, (row) => [
      row.lora,
      formatRunCount(row),
      formatDuration(row.avg_duration_ms),
    ], (row) => this.openRunGroup({ type: "lora", value: row.lora, label: row.lora }));
  }

  renderTable(headers, rows, mapRow, onRowClick = null) {
    const table = this.makeTable(headers);
    const body = table.querySelector("tbody");
    for (const row of rows) {
      const attrs = onRowClick ? { class: "is-clickable", title: "Show individual runs", onclick: () => onRowClick(row) } : {};
      body.append(el("tr", attrs, mapRow(row).map((value) => el("td", { text: String(value ?? "-") }))));
    }
    this.content.replaceChildren(rows.length ? table : this.empty("No matching records yet."));
  }

  makeTable(headers) {
    return el("table", { class: "pt-table" }, [
      el("thead", {}, el("tr", {}, headers.map((header) => el("th", { text: header })))),
      el("tbody"),
    ]);
  }

  async openRunGroup(group) {
    try {
      const queryKey = group.type === "workflow_hash" ? "workflow_hash" : group.type;
      const payload = await api(`/runs?limit=200&include_excluded=1&${queryKey}=${encodeURIComponent(group.value || "")}`);
      const dialog = el("div", { class: "pt-modal-backdrop", onclick: (event) => {
        if (event.target === dialog) dialog.remove();
      }});
      const table = this.makeGroupRunsTable(payload.runs || [], dialog, group);
      dialog.append(el("div", { class: "pt-modal pt-runs-modal" }, [
        el("header", {}, [
          el("div", {}, [
            el("h3", { text: `${group.type === "workflow_hash" ? "Workflow" : group.type === "lora" ? "LoRA" : "Model"}: ${group.label || "-"}` }),
            el("p", { class: "pt-subtle", text: `${payload.total || 0} recorded runs. Excluded runs stay in history but do not affect averages.` }),
          ]),
          el("button", { text: "x", onclick: () => dialog.remove() }),
        ]),
        table || this.empty("No runs found for this item."),
      ]));
      document.body.append(dialog);
    } catch (error) {
      this.setStatus(error.message || String(error), true);
    }
  }

  makeGroupRunsTable(rows, dialog, group) {
    if (!rows.length) return null;
    const table = this.makeTable(["When", "Duration", "Model", "Sampler", "Steps", "Resolution", "Nodes", "Avg", "Action"]);
    const body = table.querySelector("tbody");
    for (const row of rows) {
      const action = el("button", {
        class: row.excluded_from_stats ? "pt-include" : "pt-exclude",
        text: row.excluded_from_stats ? "Include" : "Exclude",
        onclick: async (event) => {
          event.stopPropagation();
          await this.setRunExcluded(row.prompt_id, !row.excluded_from_stats);
          dialog.remove();
          await this.openRunGroup(group);
          await this.refresh();
        },
      });
      const tr = el("tr", { class: row.excluded_from_stats ? "is-excluded" : "", onclick: () => this.openRun(row.prompt_id), title: "Open run detail" });
      tr.append(
        ...[
          formatDate(row.end_ts || row.start_ts),
          formatDuration(row.duration_ms),
          row.primary_model || "-",
          row.primary_sampler || "-",
          row.primary_steps ?? "-",
          row.primary_width && row.primary_height ? `${row.primary_width}x${row.primary_height} x${row.primary_batch_size || 1}` : "-",
          `${row.cached_node_count}/${row.executed_node_count}/${row.total_node_count}`,
          row.excluded_from_stats ? "Excluded" : "Included",
        ].map((value) => el("td", { text: String(value) })),
        el("td", {}, action),
      );
      body.append(tr);
    }
    return table;
  }

  async setRunExcluded(promptId, excluded) {
    await api(`/runs/${encodeURIComponent(promptId)}/exclusion`, {
      method: "POST",
      body: JSON.stringify({ excluded, note: excluded ? "Excluded from aggregate averages" : null }),
    });
  }

  async openRun(promptId) {
    try {
      const run = await api(`/runs/${encodeURIComponent(promptId)}`);
      const dialog = el("div", { class: "pt-modal-backdrop", onclick: (event) => {
        if (event.target === dialog) dialog.remove();
      }});
      const outputs = (run.outputs || []).map((o) => `${o.kind}: ${o.subfolder ? `${o.subfolder}/` : ""}${o.filename}`).join("\n") || "No output filenames recorded.";
      dialog.append(el("div", { class: "pt-modal" }, [
        el("header", {}, [
          el("h3", { text: `Run ${shortHash(run.prompt_id)}` }),
          el("button", { text: "x", onclick: () => dialog.remove() }),
        ]),
        el("div", { class: "pt-detail-grid" }, [
          this.metric("Duration", formatDuration(run.duration_ms)),
          this.metric("Model", run.primary_model || "-"),
          this.metric("Sampler", run.primary_sampler || "-"),
          this.metric("Nodes", `${run.cached_node_count}/${run.executed_node_count}/${run.total_node_count}`),
          this.metric("Averages", run.excluded_from_stats ? "Excluded" : "Included"),
        ]),
        el("div", { class: "pt-inline-actions" }, [
          el("button", { text: run.excluded_from_stats ? "Include in Averages" : "Exclude from Averages", onclick: async () => {
            await this.setRunExcluded(run.prompt_id, !run.excluded_from_stats);
            dialog.remove();
            await this.refresh();
          }}),
        ]),
        el("h4", { text: "Outputs" }),
        el("pre", { text: outputs }),
        el("h4", { text: "Extracted Factors" }),
        el("pre", { text: JSON.stringify(run.factors || {}, null, 2) }),
      ]));
      document.body.append(dialog);
    } catch (error) {
      this.setStatus(error.message || String(error), true);
    }
  }

  async clearHistory() {
    if (!confirm("Clear all local Performance Tracker history? This does not delete generated images or workflows.")) return;
    try {
      await api("/admin/clear", { method: "POST", body: "{}" });
      await this.refresh();
    } catch (error) {
      this.setStatus(error.message || String(error), true);
    }
  }

  empty(message) {
    return el("div", { class: "pt-empty", text: message });
  }

  setStatus(message, isError = false) {
    this.status.textContent = message;
    this.status.classList.toggle("is-error", Boolean(isError));
  }
}

function injectStyles() {
  if (document.getElementById("performance-tracker-styles")) return;
  document.head.append(el("style", { id: "performance-tracker-styles", text: `
    .pt-rail-button {
      position: fixed;
      left: 8px;
      bottom: 124px;
      z-index: 999;
      height: 30px;
      padding: 0 10px;
      border: 1px solid #3a3f48;
      border-radius: 6px;
      background: #20242b;
      color: #e5e7eb;
      font-size: 12px;
      cursor: pointer;
    }
    .pt-panel {
      position: fixed;
      top: 70px;
      left: 56px;
      bottom: 48px;
      width: min(920px, calc(100vw - 96px));
      z-index: 998;
      display: none;
      flex-direction: column;
      background: #16191f;
      color: #e5e7eb;
      border: 1px solid #2f3540;
      border-radius: 8px;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.55);
      overflow: hidden;
      font: 12px/1.35 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .pt-panel.is-open { display: flex; }
    .pt-header {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid #2b3038;
      background: #1b1f26;
    }
    .pt-header h2 { margin: 0; font-size: 18px; font-weight: 650; }
    .pt-header p { margin: 3px 0 0; color: #9ca3af; }
    .pt-actions, .pt-tabs { display: flex; gap: 8px; align-items: center; }
    .pt-actions button, .pt-tabs button {
      border: 1px solid #3a3f48;
      border-radius: 6px;
      background: #252a33;
      color: #e5e7eb;
      padding: 6px 10px;
      cursor: pointer;
    }
    .pt-actions .pt-danger { color: #fecaca; border-color: #6b3030; background: #3b1f24; }
    .pt-overview {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
      padding: 12px 16px;
      border-bottom: 1px solid #2b3038;
    }
    .pt-metric {
      min-width: 0;
      padding: 8px 10px;
      border: 1px solid #303640;
      border-radius: 6px;
      background: #111419;
    }
    .pt-metric span { display: block; color: #9ca3af; font-size: 11px; }
    .pt-metric strong { display: block; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pt-tabs {
      padding: 10px 16px;
      border-bottom: 1px solid #2b3038;
    }
    .pt-tabs button.is-active { background: #0d6efd; border-color: #2e86ff; color: #fff; }
    .pt-content {
      flex: 1;
      overflow: auto;
      padding: 0 16px 12px;
    }
    .pt-table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    .pt-table th, .pt-table td {
      padding: 8px 9px;
      border-bottom: 1px solid #2b3038;
      text-align: left;
      vertical-align: top;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .pt-table th {
      position: sticky;
      top: 0;
      background: #16191f;
      color: #aab2bf;
      z-index: 1;
    }
    .pt-table tbody tr:hover { background: #20242b; cursor: default; }
    .pt-table tbody tr.is-clickable:hover, .pt-table tbody tr[title]:hover { cursor: pointer; }
    .pt-table tbody tr.is-excluded { color: #8b95a5; background: rgba(75, 85, 99, 0.18); }
    .pt-table tbody tr.is-excluded td { text-decoration: none; }
    .pt-empty, .pt-status {
      padding: 12px 16px;
      color: #9ca3af;
    }
    .pt-status.is-error { color: #fecaca; }
    .pt-modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 1000;
      background: rgba(0, 0, 0, 0.45);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 32px;
    }
    .pt-modal {
      width: min(860px, 100%);
      max-height: calc(100vh - 80px);
      overflow: auto;
      background: #16191f;
      color: #e5e7eb;
      border: 1px solid #353b46;
      border-radius: 8px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.65);
      padding: 16px;
    }
    .pt-modal header { display: flex; justify-content: space-between; align-items: center; }
    .pt-modal h3, .pt-modal h4 { margin: 0 0 10px; }
    .pt-modal p { margin: 2px 0 0; }
    .pt-subtle { color: #9ca3af; }
    .pt-modal button {
      border: 1px solid #3a3f48;
      border-radius: 6px;
      background: #252a33;
      color: #e5e7eb;
      padding: 5px 9px;
      cursor: pointer;
    }
    .pt-detail-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin: 12px 0 12px; }
    .pt-inline-actions { display: flex; gap: 8px; margin: 0 0 16px; }
    .pt-runs-modal { width: min(1100px, 100%); }
    .pt-exclude { color: #fecaca !important; border-color: #6b3030 !important; background: #3b1f24 !important; }
    .pt-include { color: #bbf7d0 !important; border-color: #25633d !important; background: #173822 !important; }
    .pt-modal pre {
      max-height: 300px;
      overflow: auto;
      margin: 0 0 14px;
      padding: 10px;
      border: 1px solid #2f3540;
      border-radius: 6px;
      background: #0f1217;
      color: #d1d5db;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    @media (max-width: 760px) {
      .pt-panel { left: 8px; width: calc(100vw - 16px); }
      .pt-overview, .pt-detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .pt-header { flex-direction: column; }
    }
  ` }));
}

app.registerExtension({
  name: "ComfyUI.PerformanceTracker",
  setup() {
    injectStyles();
    const panel = new PerformanceTrackerPanel();
    panel.mount();
  },
});


