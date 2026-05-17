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
    this._confirm = null;  // { type: "install", entityId, backup, title } | { type: "restart" }
    this._t = {};
    this._restartRequired = false;
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
    await this._loadTranslations();
    await Promise.all([this._fetchUpdates(), this._fetchRestartInfo()]);
    this._subscribeStateChanges();
  }

  async _loadTranslations() {
    const lang = this._hass.locale?.language ?? "en";
    const base = "/advanced_update_manager_panel/translations/";
    try {
      const res = await fetch(`${base}${lang}.json`);
      if (!res.ok) throw new Error("not found");
      this._t = await res.json();
    } catch {
      try {
        const res = await fetch(`${base}en.json`);
        this._t = await res.json();
      } catch {
        this._t = {};
      }
    }
  }

  _tr(key, fallback = key) {
    return this._t[key] ?? fallback;
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
      this._error = this._tr("error_load", "Could not fetch updates. Is the integration loaded?");
      console.error("[AdvancedUpdateManager]", e);
    }
    this._loading = false;
    this._render();
  }

  async _fetchRestartInfo() {
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/get_restart_info",
      });
      this._restartRequired = result.restart_required || false;
    } catch (e) {
      console.error("[AdvancedUpdateManager] restart check failed", e);
    }
    this._render();
  }

  _subscribeStateChanges() {
    this._unsubscribe = this._hass.connection.subscribeEvents((event) => {
      const entityId = event.data?.entity_id;
      if (!entityId) return;

      if (entityId.startsWith("update.")) {
        const oldState = event.data?.old_state?.state;
        const newState = event.data?.new_state?.state;

        if (oldState === "on" && newState !== "on") {
          // Update installed — remove from list immediately
          this._updates = this._updates.filter((u) => u.entity_id !== entityId);
          this._render();
        } else if (newState === "on" && oldState !== "on") {
          this._fetchUpdates();
        } else if (newState === "on" && oldState === "on") {
          this._fetchUpdates();
        }
      } else if (entityId.startsWith("persistent_notification.")) {
        // A notification appeared or disappeared — re-check restart state
        this._fetchRestartInfo();
      }
    }, "state_changed");
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = null;
    }
  }

  _requestInstall(entityId, backup) {
    const update = this._updates.find((u) => u.entity_id === entityId);
    this._confirm = { type: "install", entityId, backup, title: update ? update.title : entityId };
    this._render();
  }

  _requestRestart() {
    this._confirm = { type: "restart" };
    this._render();
  }

  _cancelConfirm() {
    this._confirm = null;
    this._render();
  }

  async _doInstall() {
    const { entityId, backup } = this._confirm;
    this._confirm = null;
    await this._install(entityId, backup);
  }

  async _doRestart() {
    this._confirm = null;
    this._render();
    try {
      await this._hass.connection.sendMessagePromise({
        type: "call_service",
        domain: "homeassistant",
        service: "restart",
        service_data: {},
      });
    } catch (e) {
      console.error("[AdvancedUpdateManager] restart failed", e);
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

  _renderRestartBanner() {
    if (!this._restartRequired) return "";
    const isAdmin = this._hass.user?.is_admin ?? false;
    return `
      <div class="restart-banner">
        <span class="restart-icon">⚠</span>
        <span class="restart-text">${this._tr("restart_banner", "Home Assistant needs to be restarted for recent changes to take effect.")}</span>
        ${isAdmin
          ? `<button class="btn btn-restart" onclick="this.getRootNode().host._requestRestart()">${this._tr("restart_btn", "Restart Home Assistant")}</button>`
          : ""}
      </div>`;
  }

  _renderUpdateRow(u) {
    const inProgress = u.in_progress;
    const dateDisplay = u.release_date || "—";
    const releaseLink = u.release_url
      ? `<a href="${u.release_url}" target="_blank" rel="noopener" class="release-link" title="${this._tr("release_notes_title", "View release notes")}">↗</a>`
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
            ? `<span class="badge in-progress-badge">${this._tr("installing", "Installing…")}</span>`
            : `
              <button class="btn btn-update" data-entity="${this._escHtml(u.entity_id)}" onclick="this.getRootNode().host._requestInstall('${this._escHtml(u.entity_id)}', false)" title="${this._tr("btn_update_title", "Install update")}">${this._tr("btn_update", "Update")}</button>
              <button class="btn btn-backup" data-entity="${this._escHtml(u.entity_id)}" onclick="this.getRootNode().host._requestInstall('${this._escHtml(u.entity_id)}', true)" title="${this._tr("btn_backup_title", "Back up and install")}">${this._tr("btn_backup_update", "Backup + Update")}</button>
              <button class="btn btn-skip" onclick="this.getRootNode().host._skip('${this._escHtml(u.entity_id)}')" title="${this._tr("btn_skip_title", "Skip this version")}">${this._tr("btn_skip", "Skip")}</button>
            `}
        </td>
      </tr>`;
  }

  _renderGroups() {
    if (this._updates.length === 0) {
      return `<div class="empty-state">
        <span class="empty-icon">✓</span>
        <p>${this._tr("empty_title", "All up to date!")}</p>
      </div>`;
    }

    const groups = this._groupByType(this._updates);
    const typeOrder = ["core", "haos", "addon", "hacs", "device", "other"];
    let html = "";

    for (const type of typeOrder) {
      if (!groups[type]) continue;
      const color = this._typeColor(type);
      const label = this._typeLabel(type);
      const count = groups[type].length;
      const countLabel = count === 1
        ? `1 ${this._tr("update_count_one", "update")}`
        : `${count} ${this._tr("update_count_other", "updates")}`;
      html += `
        <div class="group">
          <div class="group-header" style="border-left: 4px solid ${color}">
            <span class="group-badge" style="background:${color}">${label}</span>
            <span class="group-count">${countLabel}</span>
          </div>
          <table class="update-table">
            <thead>
              <tr>
                <th>${this._tr("col_name", "Name")}</th>
                <th>${this._tr("col_version", "Version")}</th>
                <th>${this._tr("col_release_date", "Release date")}</th>
                <th>${this._tr("col_action", "Action")}</th>
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

  _renderConfirmModal() {
    if (!this._confirm) return "";

    if (this._confirm.type === "restart") {
      return `
        <div class="confirm-overlay">
          <div class="confirm-dialog">
            <p class="confirm-title">${this._tr("confirm_restart_title", "Confirm restart")}</p>
            <p class="confirm-body">${this._tr("confirm_restart_body", "Are you sure you want to restart Home Assistant? This will cause a brief outage.")}</p>
            <div class="confirm-actions">
              <button class="btn btn-skip" onclick="this.getRootNode().host._cancelConfirm()">${this._tr("btn_cancel", "Cancel")}</button>
              <button class="btn btn-restart-confirm" onclick="this.getRootNode().host._doRestart()">${this._tr("btn_restart", "Restart")}</button>
            </div>
          </div>
        </div>`;
    }

    const { title, backup } = this._confirm;
    const bodyKey = backup ? "confirm_body_backup" : "confirm_body_update";
    const bodyDefault = backup ? "Do you want to back up and update" : "Do you want to update";
    const actionLabel = backup ? this._tr("btn_backup_update", "Backup + Update") : this._tr("btn_update", "Update");
    return `
      <div class="confirm-overlay">
        <div class="confirm-dialog">
          <p class="confirm-title">${this._tr("confirm_title", "Confirm update")}</p>
          <p class="confirm-body">${this._tr(bodyKey, bodyDefault)} <strong>${this._escHtml(title)}</strong>?</p>
          <div class="confirm-actions">
            <button class="btn btn-skip" onclick="this.getRootNode().host._cancelConfirm()">${this._tr("btn_cancel", "Cancel")}</button>
            <button class="btn ${backup ? "btn-backup" : "btn-update"}" onclick="this.getRootNode().host._doInstall()">${actionLabel}</button>
          </div>
        </div>
      </div>`;
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
        .restart-banner { display: flex; align-items: center; gap: 12px; background: var(--warning-color, #ff9800); color: white; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; }
        .restart-icon { font-size: 1.25rem; flex-shrink: 0; }
        .restart-text { flex: 1; font-size: 0.9rem; line-height: 1.4; }
        .btn-restart { background: white; color: var(--warning-color, #ff9800); border: none; border-radius: 4px; padding: 6px 14px; cursor: pointer; font-size: 0.875rem; font-weight: 600; white-space: nowrap; flex-shrink: 0; }
        .btn-restart:hover { background: rgba(255,255,255,0.85); }
        .btn-restart-confirm { background: var(--warning-color, #ff9800); color: white; }
        .btn-restart-confirm:hover { filter: brightness(1.1); }
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
          .restart-banner { flex-wrap: wrap; }
        }
        .confirm-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000; display: flex; align-items: center; justify-content: center; }
        .confirm-dialog { background: var(--card-background-color, white); border-radius: 8px; padding: 24px; max-width: 400px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }
        .confirm-title { margin: 0 0 12px; font-size: 1.1rem; font-weight: 500; color: var(--primary-text-color); }
        .confirm-body { margin: 0 0 20px; color: var(--secondary-text-color); line-height: 1.5; }
        .confirm-actions { display: flex; gap: 8px; justify-content: flex-end; }
      </style>
      <div class="header">
        <h1>${this._tr("panel_title", "Update Manager")}</h1>
        <div class="header-actions">
          <button class="ha-update-btn" onclick="this.getRootNode().host._navigateToHaUpdates()">${this._tr("ha_updates_btn", "HA Updates ↗")}</button>
          <button class="refresh-btn" onclick="this.getRootNode().host._fetchUpdates()">${this._tr("refresh_btn", "Refresh list")}</button>
        </div>
      </div>
      ${this._renderRestartBanner()}
      ${this._loading
        ? `<div class="loading">${this._tr("loading", "Fetching updates…")}</div>`
        : this._error
          ? `<div class="error">${this._error}</div>`
          : this._renderGroups()}
      ${this._renderConfirmModal()}
    `;
  }
}

customElements.define("advanced-update-manager-panel", AdvancedUpdateManagerPanel);
