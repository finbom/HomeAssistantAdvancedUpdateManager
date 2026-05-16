/**
 * Advanced Update Manager — Custom Panel
 * Displays all pending HA updates with type, release date, and action buttons.
 */
class AdvancedUpdateManagerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._updates = [];
    this._loading = true;
    this._error = null;
    this._unsubscribe = null;
    this._initialized = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._init();
    }
  }

  // panel property required by HA custom panel API
  set panel(_panel) {}

  async _init() {
    this._render();
    await this._fetchUpdates();
    this._subscribeStateChanges();
  }

  async _fetchUpdates() {
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/get_updates",
      });
      this._updates = result.updates || [];
    } catch (e) {
      this._error = "Kunde inte hämta uppdateringar. Är integrationen laddad?";
      console.error("[AdvancedUpdateManager]", e);
    }
    this._loading = false;
    this._render();
  }

  _subscribeStateChanges() {
    this._unsubscribe = this._hass.connection.subscribeEvents((event) => {
      const entityId = event.data?.entity_id;
      if (!entityId?.startsWith("update.")) return;

      const oldState = event.data?.old_state?.state;
      const newState = event.data?.new_state?.state;

      if (oldState === "on" && newState !== "on") {
        // Update installed — remove from list immediately
        this._updates = this._updates.filter((u) => u.entity_id !== entityId);
        this._render();
      } else if (newState === "on" && oldState !== "on") {
        // New update appeared — do a full refresh
        this._fetchUpdates();
      } else if (newState === "on" && oldState === "on") {
        // in_progress state may have changed — refresh
        this._fetchUpdates();
      }
    }, "state_changed");
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = null;
    }
  }

  async _install(entityId, backup) {
    const btn = this.shadowRoot.querySelector(`[data-entity="${entityId}"]`);
    if (btn) btn.disabled = true;
    try {
      await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/install_update",
        entity_id: entityId,
        backup,
      });
    } catch (e) {
      console.error("[AdvancedUpdateManager] install failed", e);
    }
    // State change subscription will update the list
  }

  _navigateToHaUpdates() {
    history.pushState(null, "", "/update");
    window.dispatchEvent(new Event("location-changed"));
  }

  async _skip(entityId) {
    try {
      await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/skip_update",
        entity_id: entityId,
      });
      this._updates = this._updates.filter((u) => u.entity_id !== entityId);
      this._render();
    } catch (e) {
      console.error("[AdvancedUpdateManager] skip failed", e);
    }
  }

  _typeLabel(type) {
    return { core: "Core", haos: "HA OS", addon: "Add-on", hacs: "HACS", device: "Device", other: "Other" }[type] || type;
  }

  _typeColor(type) {
    return { core: "#03a9f4", haos: "#4caf50", addon: "#ff9800", hacs: "#9c27b0", device: "#607d8b", other: "#9e9e9e" }[type] || "#9e9e9e";
  }

  _groupByType(updates) {
    const groups = {};
    for (const u of updates) {
      if (!groups[u.type]) groups[u.type] = [];
      groups[u.type].push(u);
    }
    return groups;
  }

  _renderUpdateRow(u) {
    const inProgress = u.in_progress;
    const dateDisplay = u.release_date || "—";
    const releaseLink = u.release_url
      ? `<a href="${u.release_url}" target="_blank" rel="noopener" class="release-link" title="Se release notes">↗</a>`
      : "";

    return `
      <tr class="update-row${inProgress ? " in-progress" : ""}">
        <td class="name-cell">
          <span class="title">${this._escHtml(u.title)}</span>
        </td>
        <td class="version-cell">
          <span class="version-from">${this._escHtml(u.installed_version)}</span>
          <span class="arrow">→</span>
          <span class="version-to">${this._escHtml(u.latest_version)}</span>
        </td>
        <td class="date-cell">${dateDisplay} ${releaseLink}</td>
        <td class="action-cell">
          ${inProgress
            ? `<span class="badge in-progress-badge">Installerar…</span>`
            : `
              <button class="btn btn-update" data-entity="${this._escHtml(u.entity_id)}" onclick="this.getRootNode().host._install('${this._escHtml(u.entity_id)}', false)" title="Installera uppdatering">Uppdatera</button>
              <button class="btn btn-backup" data-entity="${this._escHtml(u.entity_id)}" onclick="this.getRootNode().host._install('${this._escHtml(u.entity_id)}', true)" title="Säkerhetskopiera och installera">Backup + Uppdatera</button>
              <button class="btn btn-skip" onclick="this.getRootNode().host._skip('${this._escHtml(u.entity_id)}')" title="Hoppa över denna version">Hoppa över</button>
            `}
        </td>
      </tr>`;
  }

  _renderGroups() {
    if (this._updates.length === 0) {
      return `<div class="empty-state">
        <span class="empty-icon">✓</span>
        <p>Allt är uppdaterat!</p>
      </div>`;
    }

    const groups = this._groupByType(this._updates);
    const typeOrder = ["core", "haos", "addon", "hacs", "device", "other"];
    let html = "";

    for (const type of typeOrder) {
      if (!groups[type]) continue;
      const color = this._typeColor(type);
      const label = this._typeLabel(type);
      html += `
        <div class="group">
          <div class="group-header" style="border-left: 4px solid ${color}">
            <span class="group-badge" style="background:${color}">${label}</span>
            <span class="group-count">${groups[type].length} uppdatering${groups[type].length !== 1 ? "ar" : ""}</span>
          </div>
          <table class="update-table">
            <thead>
              <tr>
                <th>Namn</th>
                <th>Version</th>
                <th>Release-datum</th>
                <th>Åtgärd</th>
              </tr>
            </thead>
            <tbody>
              ${groups[type].map((u) => this._renderUpdateRow(u)).join("")}
            </tbody>
          </table>
        </div>`;
    }
    return html;
  }

  _escHtml(str) {
    return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 16px; font-family: var(--paper-font-body1_-_font-family, sans-serif); }
        .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
        .header h1 { margin: 0; font-size: 1.5rem; font-weight: 500; color: var(--primary-text-color); }
        .header-actions { display: flex; gap: 8px; align-items: center; }
        .refresh-btn { background: none; border: 1px solid var(--primary-color, #03a9f4); color: var(--primary-color, #03a9f4); border-radius: 4px; padding: 6px 14px; cursor: pointer; font-size: 0.875rem; }
        .refresh-btn:hover { background: var(--primary-color, #03a9f4); color: white; }
        .ha-update-btn { background: none; border: 1px solid var(--divider-color, #e0e0e0); color: var(--secondary-text-color); border-radius: 4px; padding: 6px 14px; cursor: pointer; font-size: 0.875rem; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; }
        .ha-update-btn:hover { background: var(--secondary-background-color, #f5f5f5); }
        .loading, .error { text-align: center; padding: 48px; color: var(--secondary-text-color); }
        .error { color: var(--error-color, #db4437); }
        .group { margin-bottom: 24px; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.1); background: var(--card-background-color, white); }
        .group-header { display: flex; align-items: center; gap: 10px; padding: 12px 16px; background: var(--secondary-background-color, #f5f5f5); }
        .group-badge { color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
        .group-count { font-size: 0.875rem; color: var(--secondary-text-color); }
        .update-table { width: 100%; border-collapse: collapse; }
        .update-table th { text-align: left; padding: 10px 16px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--secondary-text-color); border-bottom: 1px solid var(--divider-color, #e0e0e0); }
        .update-table td { padding: 12px 16px; border-bottom: 1px solid var(--divider-color, #e0e0e0); vertical-align: middle; }
        .update-row:last-child td { border-bottom: none; }
        .update-row.in-progress { opacity: 0.7; }
        .title { font-weight: 500; color: var(--primary-text-color); }
        .version-from { color: var(--secondary-text-color); font-size: 0.875rem; }
        .arrow { margin: 0 6px; color: var(--secondary-text-color); }
        .version-to { color: var(--primary-color, #03a9f4); font-weight: 500; font-size: 0.875rem; }
        .date-cell { font-size: 0.875rem; color: var(--secondary-text-color); white-space: nowrap; }
        .release-link { margin-left: 4px; text-decoration: none; color: var(--primary-color, #03a9f4); }
        .action-cell { white-space: nowrap; }
        .btn { border: none; border-radius: 4px; padding: 6px 12px; cursor: pointer; font-size: 0.8rem; margin-right: 4px; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-update { background: var(--primary-color, #03a9f4); color: white; }
        .btn-update:hover:not(:disabled) { filter: brightness(1.1); }
        .btn-backup { background: var(--success-color, #4caf50); color: white; }
        .btn-backup:hover:not(:disabled) { filter: brightness(1.1); }
        .btn-skip { background: var(--secondary-background-color, #f5f5f5); color: var(--secondary-text-color); border: 1px solid var(--divider-color, #e0e0e0); }
        .btn-skip:hover { background: var(--divider-color, #e0e0e0); }
        .badge { padding: 4px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
        .in-progress-badge { background: var(--warning-color, #ff9800); color: white; }
        .empty-state { text-align: center; padding: 64px; color: var(--secondary-text-color); }
        .empty-icon { font-size: 3rem; display: block; margin-bottom: 12px; color: var(--success-color, #4caf50); }
        @media (max-width: 600px) {
          .btn-backup, .btn-skip { display: none; }
          .update-table th:nth-child(3), .update-table td:nth-child(3) { display: none; }
        }
      </style>
      <div class="header">
        <h1>Update Manager</h1>
        <div class="header-actions">
          <button class="ha-update-btn" onclick="this.getRootNode().host._navigateToHaUpdates()">HA Updates ↗</button>
          <button class="refresh-btn" onclick="this.getRootNode().host._fetchUpdates()">Uppdatera lista</button>
        </div>
      </div>
      ${this._loading
        ? `<div class="loading">Hämtar uppdateringar…</div>`
        : this._error
          ? `<div class="error">${this._error}</div>`
          : this._renderGroups()}
    `;
  }
}

customElements.define("advanced-update-manager-panel", AdvancedUpdateManagerPanel);
