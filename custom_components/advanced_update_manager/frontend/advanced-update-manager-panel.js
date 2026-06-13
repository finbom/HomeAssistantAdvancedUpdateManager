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
    this._confirm = null;  // { type: "install"|"restart"|"skip", entityId?, backup?, backupType?, updateType?, title? }
    this._installing = new Set();
    this._installingWithBackup = new Set();
    this._t = {};
    this._config = {};
    this._restartRequired = false;
    this._showSkipped = false;
    this._skippedUpdates = [];
    this._sortBy = "type";   // "type" | "date"
    this._sortDir = "asc";   // "asc" | "desc"
    this._view = "pending";  // "pending" | "installed" | "history"
    this._installed = [];
    this._installedLoading = false;
    this._installedSortBy = "type";   // "name" | "type" | "release_date" | "install_date"
    this._installedSortDir = "asc";
    this._installedFilter = "";
    this._historyEvents = [];
    this._historyOldestDate = null;
    this._historyRecorderAvailable = true;
    this._historyLoading = false;
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
    await Promise.all([this._fetchUpdates(), this._fetchRestartInfo(), this._loadConfig()]);
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

  async _loadConfig() {
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/get_config",
      });
      this._config = result;
    } catch {
      this._config = { default_backup_type: "full" };
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

  async _fetchInstalled() {
    this._installedLoading = true;
    this._render();
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/get_installed",
      });
      this._installed = result.installed || [];
    } catch (e) {
      console.error("[AdvancedUpdateManager] installed fetch failed", e);
      this._installed = [];
    }
    this._installedLoading = false;
    this._render();
  }

  async _fetchHistory() {
    this._historyLoading = true;
    this._render();
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/get_history",
      });
      this._historyEvents = result.events || [];
      this._historyOldestDate = result.oldest_date || null;
      this._historyRecorderAvailable = result.recorder_available !== false;
    } catch (e) {
      console.error("[AdvancedUpdateManager] history fetch failed", e);
      this._historyEvents = [];
      this._historyRecorderAvailable = false;
    }
    this._historyLoading = false;
    this._render();
  }

  async _setView(view) {
    if (this._view === view) return;
    this._view = view;
    this._render();
    if (view === "installed" && this._installed.length === 0 && !this._installedLoading) {
      await this._fetchInstalled();
    } else if (view === "history" && this._historyEvents.length === 0 && !this._historyLoading) {
      await this._fetchHistory();
    }
  }

  async _refresh() {
    if (this._view === "pending") {
      await Promise.all([this._fetchUpdates(), this._fetchRestartInfo()]);
    } else if (this._view === "installed") {
      this._installed = [];
      await this._fetchInstalled();
    } else if (this._view === "history") {
      this._historyEvents = [];
      this._historyOldestDate = null;
      await this._fetchHistory();
    }
  }

  _subscribeStateChanges() {
    this._unsubscribe = this._hass.connection.subscribeEvents((event) => {
      const entityId = event.data?.entity_id;
      if (!entityId) return;

      if (entityId.startsWith("update.")) {
        const oldState = event.data?.old_state?.state;
        const newState = event.data?.new_state?.state;

        if (oldState === "on" && newState !== "on") {
          this._updates = this._updates.filter((u) => u.entity_id !== entityId);
          this._installing.delete(entityId);
          this._installingWithBackup.delete(entityId);
          this._render();
          this._fetchRestartInfo();
          // Invalidate cached installed/history so they reload on next view
          this._installed = [];
          this._historyEvents = [];
          this._historyOldestDate = null;
        } else if (newState === "on" && oldState !== "on") {
          this._fetchUpdates();
          this._installed = [];
        } else if (newState === "on" && oldState === "on") {
          this._fetchUpdates();
        }
      } else if (entityId.startsWith("persistent_notification.")) {
        this._fetchRestartInfo();
      }
    }, "state_changed");

    this._onConnectionReady = () => {
      this._fetchRestartInfo();
      this._fetchUpdates();
    };
    this._hass.connection.addEventListener("ready", this._onConnectionReady);
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = null;
    }
    if (this._onConnectionReady) {
      this._hass.connection.removeEventListener("ready", this._onConnectionReady);
      this._onConnectionReady = null;
    }
  }

  _setSort(field) {
    if (this._sortBy === field) {
      this._sortDir = this._sortDir === "asc" ? "desc" : "asc";
    } else {
      this._sortBy = field;
      this._sortDir = field === "date" ? "desc" : "asc";
    }
    this._render();
  }

  _setInstalledFilter(value) {
    this._installedFilter = value;
    this._render();
    const input = this.shadowRoot.querySelector(".search-input");
    if (input) {
      input.focus();
      const len = input.value.length;
      input.setSelectionRange(len, len);
    }
  }

  _setInstalledSort(field) {
    if (this._installedSortBy === field) {
      this._installedSortDir = this._installedSortDir === "asc" ? "desc" : "asc";
    } else {
      this._installedSortBy = field;
      this._installedSortDir = (field === "install_date" || field === "release_date") ? "desc" : "asc";
    }
    this._render();
  }

  _requestInstall(entityId, backup) {
    const update = this._updates.find((u) => u.entity_id === entityId);
    this._confirm = {
      type: "install",
      entityId,
      backup,
      updateType: update ? update.type : "other",
      title: update ? update.title : entityId,
    };
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

  async _doInstall(backupType) {
    const { entityId, backup } = this._confirm;
    this._confirm = null;
    this._installing.add(entityId);
    if (backup) this._installingWithBackup.add(entityId);
    this._render();
    await this._install(entityId, backup, backupType || "full");
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

  async _install(entityId, backup, backupType) {
    try {
      await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/install_update",
        entity_id: entityId,
        backup,
        backup_type: backupType || "full",
      });
    } catch (e) {
      console.error("[AdvancedUpdateManager] install failed", e);
      this._installing.delete(entityId);
      this._installingWithBackup.delete(entityId);
      this._render();
    }
  }

  _navigateToHaUpdates() {
    history.pushState(null, "", "/config/updates");
    window.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, cancelable: false, detail: { replace: false } }));
  }

  _requestSkip(entityId) {
    const update = this._updates.find((u) => u.entity_id === entityId);
    this._confirm = { type: "skip", entityId, title: update ? update.title : entityId };
    this._render();
  }

  async _doSkip() {
    const { entityId } = this._confirm;
    this._confirm = null;
    this._render();
    try {
      await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/skip_update",
        entity_id: entityId,
      });
      this._updates = this._updates.filter((u) => u.entity_id !== entityId);
      if (this._showSkipped) await this._fetchSkippedUpdates();
      this._render();
    } catch (e) {
      console.error("[AdvancedUpdateManager] skip failed", e);
    }
  }

  async _fetchSkippedUpdates() {
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/get_skipped_updates",
      });
      this._skippedUpdates = result.updates || [];
    } catch (e) {
      console.error("[AdvancedUpdateManager] skipped fetch failed", e);
      this._skippedUpdates = [];
    }
  }

  async _toggleSkipped() {
    this._showSkipped = !this._showSkipped;
    if (this._showSkipped) {
      await this._fetchSkippedUpdates();
    }
    this._render();
  }

  async _unskip(entityId) {
    try {
      await this._hass.connection.sendMessagePromise({
        type: "call_service",
        domain: "update",
        service: "clear_skipped",
        service_data: { entity_id: entityId },
      });
      this._skippedUpdates = this._skippedUpdates.filter((u) => u.entity_id !== entityId);
      this._render();
    } catch (e) {
      console.error("[AdvancedUpdateManager] unskip failed", e);
    }
  }

  _typeLabel(type) {
    return { core: "Core", haos: "HA OS", addon: "Apps", hacs: "HACS", device: "Device", other: "Other" }[type] || type;
  }

  _typeColor(type) {
    return { core: "#03a9f4", haos: "#4caf50", addon: "#ff9800", hacs: "#9c27b0", device: "#607d8b", other: "#9e9e9e" }[type] || "#9e9e9e";
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

  _renderUpdateRow(u, showTypeBadge = false) {
    const isInstalling = this._installing.has(u.entity_id) || u.in_progress;
    const isBackupInstall = this._installingWithBackup.has(u.entity_id);
    const isNotInstallable = u.installable === false;
    const dateDisplay = u.release_date || "—";
    const releaseLink = u.release_url
      ? `<a href="${u.release_url}" target="_blank" rel="noopener" class="release-link" title="${this._tr("release_notes_title", "View release notes")}">↗</a>`
      : "";
    const majorBadge = u.major_version_change
      ? `<span class="major-badge" title="${this._tr("major_version_title", "Major version update — may contain breaking changes")}">⚠ Major</span>`
      : "";
    const typeBadge = showTypeBadge
      ? `<span class="type-chip" style="background:${this._typeColor(u.type)}">${this._typeLabel(u.type)}</span>`
      : "";

    const notInstallableChip = isNotInstallable
      ? (() => {
          const reason = u.min_ha_version
            ? `${this._tr("not_installable_requires_ha", "Requires HA")} ${this._escHtml(u.min_ha_version)}`
            : this._tr("not_installable_tooltip", "This update cannot be installed right now");
          return `<span class="not-installable-chip" title="${reason}">⚠ ${this._tr("not_installable_label", "Not installable")}</span>`;
        })()
      : "";

    const installingText = isBackupInstall
      ? this._tr("installing_backup", "Creating backup & installing update…")
      : this._tr("installing", "Installing…");

    const actionCell = isInstalling
      ? `<div class="installing-state">
           <span class="badge in-progress-badge">${installingText}</span>
           <div class="progress-bar"></div>
         </div>`
      : isNotInstallable
        ? (() => {
            const reasonText = u.min_ha_version
              ? `<span class="not-installable-reason">${this._tr("not_installable_requires_ha", "Requires HA")} ${this._escHtml(u.min_ha_version)}</span>`
              : "";
            return `<div class="not-installable-action">
              ${reasonText}
              <button class="btn btn-skip" onclick="this.getRootNode().host._requestSkip('${this._escHtml(u.entity_id)}')" title="${this._tr("btn_skip_title", "Skip this version")}">${this._tr("btn_skip", "Skip")}</button>
            </div>`;
          })()
        : `
          <button class="btn btn-update" onclick="this.getRootNode().host._requestInstall('${this._escHtml(u.entity_id)}', false)" title="${this._tr("btn_update_title", "Install update")}">${this._tr("btn_update", "Update")}</button>
          <button class="btn btn-backup" onclick="this.getRootNode().host._requestInstall('${this._escHtml(u.entity_id)}', true)" title="${this._tr("btn_backup_title", "Back up and install")}">${this._tr("btn_backup_update", "Backup + Update")}</button>
          <button class="btn btn-skip" onclick="this.getRootNode().host._requestSkip('${this._escHtml(u.entity_id)}')" title="${this._tr("btn_skip_title", "Skip this version")}">${this._tr("btn_skip", "Skip")}</button>
        `;

    return `
      <tr class="update-row${isInstalling ? " installing" : ""}${isNotInstallable ? " not-installable-row" : ""}">
        <td class="name-cell">
          ${typeBadge}<span class="title">${this._escHtml(u.title)}</span>${notInstallableChip}
        </td>
        <td class="version-cell">
          <span class="version-from">${this._escHtml(u.installed_version)}</span>
          <span class="arrow">→</span>
          <span class="version-to">${this._escHtml(u.latest_version)}</span>
          ${majorBadge}
        </td>
        <td class="date-cell">${dateDisplay} ${releaseLink}</td>
        <td class="action-cell">${actionCell}</td>
      </tr>`;
  }

  _renderUpdates() {
    if (this._updates.length === 0) {
      return `<div class="empty-state">
        <span class="empty-icon">✓</span>
        <p>${this._tr("empty_title", "All up to date!")}</p>
      </div>`;
    }
    return this._sortBy === "date" ? this._renderFlat() : this._renderGrouped();
  }

  _renderGrouped() {
    const typeOrder = ["core", "haos", "addon", "hacs", "device", "other"];
    const orderedTypes = this._sortDir === "asc" ? typeOrder : [...typeOrder].reverse();

    const groups = {};
    for (const u of this._updates) {
      if (!groups[u.type]) groups[u.type] = [];
      groups[u.type].push(u);
    }

    let html = "";
    for (const type of orderedTypes) {
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
              ${groups[type].map((u) => this._renderUpdateRow(u, false)).join("")}
            </tbody>
          </table>
        </div>`;
    }
    return html;
  }

  _renderFlat() {
    const sorted = [...this._updates].sort((a, b) => {
      const aHasDate = !!a.release_date;
      const bHasDate = !!b.release_date;
      if (aHasDate !== bHasDate) return aHasDate ? -1 : 1;
      if (!aHasDate) return 0;
      const cmp = a.release_date.localeCompare(b.release_date);
      return this._sortDir === "asc" ? cmp : -cmp;
    });

    return `
      <div class="group">
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
            ${sorted.map((u) => this._renderUpdateRow(u, true)).join("")}
          </tbody>
        </table>
      </div>`;
  }

  _renderInstalledSortButtons() {
    const arrow = this._installedSortDir === "asc" ? "↑" : "↓";
    const fields = [
      { id: "name",         label: this._tr("col_name", "Name") },
      { id: "type",         label: this._tr("col_type", "Type") },
      { id: "release_date", label: this._tr("col_release_date", "Release date") },
      { id: "install_date", label: this._tr("col_install_date", "Install date") },
    ];
    return `
      <div class="sort-group">
        <span class="sort-label">${this._tr("sort_label", "Sort")}:</span>
        ${fields.map(f => {
          const active = this._installedSortBy === f.id;
          return `<button class="sort-btn${active ? " active" : ""}" onclick="this.getRootNode().host._setInstalledSort('${f.id}')">${f.label}${active ? ` ${arrow}` : ""}</button>`;
        }).join("")}
      </div>`;
  }

  _renderInstalled() {
    if (this._installedLoading) {
      return `<div class="loading">${this._tr("loading", "Fetching updates…")}</div>`;
    }
    if (this._installed.length === 0) {
      return `<div class="empty-state">
        <span class="empty-icon">✓</span>
        <p>${this._tr("installed_empty", "No installed updates found.")}</p>
      </div>`;
    }

    const needle = this._installedFilter.toLowerCase();
    const filtered = needle
      ? this._installed.filter(u => u.title.toLowerCase().includes(needle))
      : this._installed;

    const typeOrder = { core: 0, haos: 1, addon: 2, hacs: 3, device: 4, other: 5 };
    const sorted = [...filtered].sort((a, b) => {
      const dir = this._installedSortDir === "asc" ? 1 : -1;
      switch (this._installedSortBy) {
        case "type": {
          const tc = (typeOrder[a.type] ?? 99) - (typeOrder[b.type] ?? 99);
          return tc !== 0 ? tc * dir : a.title.toLowerCase().localeCompare(b.title.toLowerCase());
        }
        case "release_date": {
          const aD = a.release_date || "";
          const bD = b.release_date || "";
          if (!aD && !bD) return 0;
          if (!aD) return 1;
          if (!bD) return -1;
          return aD.localeCompare(bD) * dir;
        }
        case "install_date": {
          const aI = a.install_date || "";
          const bI = b.install_date || "";
          if (!aI && !bI) return 0;
          if (!aI) return 1;
          if (!bI) return -1;
          return aI.localeCompare(bI) * dir;
        }
        default:
          return a.title.toLowerCase().localeCompare(b.title.toLowerCase()) * dir;
      }
    });

    const rows = sorted.map((u) => {
      const releaseLink = u.release_url
        ? `<a href="${u.release_url}" target="_blank" rel="noopener" class="release-link" title="${this._tr("release_notes_title", "View release notes")}">↗</a>`
        : "";
      return `
      <tr>
        <td class="name-cell">
          <span class="type-chip" style="background:${this._typeColor(u.type)}">${this._typeLabel(u.type)}</span>
          <span class="title">${this._escHtml(u.title)}</span>
        </td>
        <td class="version-cell"><span class="version-to">${this._escHtml(u.installed_version)}</span></td>
        <td class="date-cell">${u.release_date || "—"} ${releaseLink}</td>
        <td class="date-cell">${u.install_date || "—"}</td>
      </tr>`;
    }).join("");

    const countLabel = needle
      ? `${sorted.length} / ${this._installed.length}`
      : `${this._installed.length}`;

    return `
      <div style="margin-bottom:12px">
        <div class="search-bar">
          <input
            type="text"
            class="search-input"
            placeholder="${this._tr("search_placeholder", "Filter by name…")}"
            value="${this._escHtml(this._installedFilter)}"
            oninput="this.getRootNode().host._setInstalledFilter(this.value)"
          />
          <span class="search-count">${countLabel}</span>
        </div>
        ${this._renderInstalledSortButtons()}
      </div>
      <div class="group">
        <table class="update-table">
          <thead><tr>
            <th>${this._tr("col_name", "Name")}</th>
            <th>${this._tr("col_installed_version", "Installed version")}</th>
            <th>${this._tr("col_release_date", "Release date")}</th>
            <th>${this._tr("col_install_date", "Install date")}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  _formatBytes(bytes) {
    if (!bytes) return "0 B";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  _renderHistory() {
    if (this._historyLoading) {
      return `<div class="loading">${this._tr("loading", "Fetching updates…")}</div>`;
    }

    const keepDays = this._config.history_keep_days ?? 365;
    const retention = keepDays > 0
      ? `${keepDays} ${this._tr("history_info_days", "days")}`
      : this._tr("history_keep_forever", "Forever");
    const storageSize = this._formatBytes(this._config.storage_size_bytes || 0);
    const count = this._historyEvents.length;
    const infoNote = `${count} ${this._tr("history_installs_tracked", "installs tracked by AUM")} — ${this._tr("history_retention_label", "Retention:")} ${retention} — ${this._tr("storage_size_label", "Storage:")} ${storageSize}`;
    const infoHtml = `<div class="history-info">${infoNote}</div>`;

    if (count === 0) {
      return `${infoHtml}<div class="empty-state">
        <p>${this._tr("history_empty", "No install history found.")}</p>
      </div>`;
    }

    const rows = this._historyEvents.map((e) => {
      const releaseLink = e.release_url
        ? `<a href="${e.release_url}" target="_blank" rel="noopener" class="release-link" title="${this._tr("release_notes_title", "View release notes")}">↗</a>`
        : "";
      return `
      <tr>
        <td class="name-cell">
          <span class="type-chip" style="background:${this._typeColor(e.type || "other")}">${this._typeLabel(e.type || "other")}</span>
          <span class="title">${this._escHtml(e.title)}</span>
        </td>
        <td class="version-cell">
          <span class="version-from">${this._escHtml(e.from_version)}</span>
          <span class="arrow">→</span>
          <span class="version-to">${this._escHtml(e.to_version)}</span>
          ${releaseLink}
        </td>
        <td class="date-cell">${e.date}</td>
      </tr>`;
    }).join("");

    return `
      ${infoHtml}
      <div class="group">
        <table class="update-table">
          <thead><tr>
            <th>${this._tr("col_name", "Name")}</th>
            <th>${this._tr("col_version", "Version")}</th>
            <th>${this._tr("col_install_date", "Install date")}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  _renderSkippedSection() {
    if (!this._showSkipped) return "";
    if (this._skippedUpdates.length === 0) {
      return `<div class="group" style="margin-top:8px">
        <div class="group-header" style="border-left:4px solid var(--secondary-text-color,#9e9e9e)">
          <span class="group-badge" style="background:var(--secondary-text-color,#9e9e9e)">${this._tr("skipped_label", "Skipped")}</span>
        </div>
        <div style="padding:24px;text-align:center;color:var(--secondary-text-color)">${this._tr("skipped_empty", "No skipped updates.")}</div>
      </div>`;
    }
    const rows = this._skippedUpdates.map((u) => `
      <tr>
        <td class="name-cell"><span class="title">${this._escHtml(u.title)}</span></td>
        <td class="version-cell"><span class="version-to">${this._escHtml(u.skipped_version)}</span></td>
        <td class="action-cell">
          ${u.release_url ? `<a href="${u.release_url}" target="_blank" rel="noopener" class="btn btn-skip" style="text-decoration:none;margin-right:4px">${this._tr("btn_release_notes", "Notes ↗")}</a>` : ""}
          <button class="btn btn-update" onclick="this.getRootNode().host._unskip('${this._escHtml(u.entity_id)}')">${this._tr("btn_unskip", "Restore")}</button>
        </td>
      </tr>`).join("");
    const count = this._skippedUpdates.length;
    const countLabel = count === 1
      ? `1 ${this._tr("update_count_one", "update")}`
      : `${count} ${this._tr("update_count_other", "updates")}`;
    return `
      <div class="group" style="margin-top:8px">
        <div class="group-header" style="border-left:4px solid var(--secondary-text-color,#9e9e9e)">
          <span class="group-badge" style="background:var(--secondary-text-color,#9e9e9e)">${this._tr("skipped_label", "Skipped")}</span>
          <span class="group-count">${countLabel}</span>
        </div>
        <table class="update-table">
          <thead><tr>
            <th>${this._tr("col_name", "Name")}</th>
            <th>${this._tr("col_version", "Skipped version")}</th>
            <th>${this._tr("col_action", "Action")}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  _renderConfirmModal() {
    if (!this._confirm) return "";

    if (this._confirm.type === "skip") {
      return `
        <div class="confirm-overlay">
          <div class="confirm-dialog">
            <p class="confirm-title">${this._tr("confirm_skip_title", "Skip this update?")}</p>
            <p class="confirm-body">${this._tr("confirm_skip_body", "Skip")} <strong>${this._escHtml(this._confirm.title)}</strong>? ${this._tr("confirm_skip_hint", "You can restore it later from the skipped list.")}</p>
            <div class="confirm-actions">
              <button class="btn btn-skip" onclick="this.getRootNode().host._cancelConfirm()">${this._tr("btn_cancel", "Cancel")}</button>
              <button class="btn btn-skip-confirm" onclick="this.getRootNode().host._doSkip()">${this._tr("btn_skip", "Skip")}</button>
            </div>
          </div>
        </div>`;
    }

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

    const { title, backup, updateType } = this._confirm;
    if (!backup) {
      return `
        <div class="confirm-overlay">
          <div class="confirm-dialog">
            <p class="confirm-title">${this._tr("confirm_title", "Confirm update")}</p>
            <p class="confirm-body">${this._tr("confirm_body_update", "Do you want to update")} <strong>${this._escHtml(title)}</strong>?</p>
            <div class="confirm-actions">
              <button class="btn btn-skip" onclick="this.getRootNode().host._cancelConfirm()">${this._tr("btn_cancel", "Cancel")}</button>
              <button class="btn btn-update" onclick="this.getRootNode().host._doInstall()">${this._tr("btn_update", "Update")}</button>
            </div>
          </div>
        </div>`;
    }

    const isHacs = updateType === "hacs";
    return `
      <div class="confirm-overlay">
        <div class="confirm-dialog">
          <p class="confirm-title">${this._tr("confirm_title", "Confirm update")}</p>
          <p class="confirm-body">${this._tr("confirm_body_backup", "Do you want to back up and update")} <strong>${this._escHtml(title)}</strong>?</p>
          <div class="confirm-actions">
            <button class="btn btn-skip" onclick="this.getRootNode().host._cancelConfirm()">${this._tr("btn_cancel", "Cancel")}</button>
            ${!isHacs ? `<button class="btn btn-backup" onclick="this.getRootNode().host._doInstall('addon_only')">${this._tr("btn_addon_backup_install", "Addon backup + Install")}</button>` : ""}
            <button class="btn btn-backup" onclick="this.getRootNode().host._doInstall('full')">${this._tr("btn_full_backup_install", "Full backup + Install")}</button>
          </div>
        </div>
      </div>`;
  }

  _escHtml(str) {
    return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  _renderSortButtons() {
    const arrow = this._sortDir === "asc" ? "↑" : "↓";
    const typeActive = this._sortBy === "type";
    const dateActive = this._sortBy === "date";
    return `
      <div class="sort-group">
        <span class="sort-label">${this._tr("sort_label", "Sort")}:</span>
        <button class="sort-btn${typeActive ? " active" : ""}" onclick="this.getRootNode().host._setSort('type')">
          ${this._tr("sort_type", "Type")}${typeActive ? ` ${arrow}` : ""}
        </button>
        <button class="sort-btn${dateActive ? " active" : ""}" onclick="this.getRootNode().host._setSort('date')">
          ${this._tr("sort_date", "Release date")}${dateActive ? ` ${arrow}` : ""}
        </button>
      </div>`;
  }

  _render() {
    const isPending = this._view === "pending";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 16px; font-family: var(--paper-font-body1_-_font-family, sans-serif); }
        .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
        .header h1 { margin: 0; font-size: 1.5rem; font-weight: 500; color: var(--primary-text-color); }
        .header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .tab-bar { display: flex; gap: 0; margin-bottom: 20px; border-bottom: 2px solid var(--divider-color, #e0e0e0); }
        .tab-btn { background: none; border: none; border-bottom: 2px solid transparent; margin-bottom: -2px; padding: 8px 18px; cursor: pointer; font-size: 0.875rem; color: var(--secondary-text-color); white-space: nowrap; transition: color 0.15s; }
        .tab-btn:hover { color: var(--primary-text-color); }
        .tab-btn.active { color: var(--primary-color, #03a9f4); border-bottom-color: var(--primary-color, #03a9f4); font-weight: 600; }
        .search-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .search-input { flex: 1; max-width: 320px; padding: 6px 10px; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 4px; background: var(--card-background-color, white); color: var(--primary-text-color); font-size: 0.875rem; outline: none; }
        .search-input:focus { border-color: var(--primary-color, #03a9f4); }
        .search-count { font-size: 0.8rem; color: var(--secondary-text-color); white-space: nowrap; }
        .sort-group { display: flex; align-items: center; gap: 4px; }
        .sort-label { font-size: 0.8rem; color: var(--secondary-text-color); white-space: nowrap; }
        .sort-btn { background: none; border: 1px solid var(--divider-color, #e0e0e0); color: var(--secondary-text-color); border-radius: 4px; padding: 6px 12px; cursor: pointer; font-size: 0.8rem; white-space: nowrap; transition: all 0.15s; }
        .sort-btn:hover { background: var(--secondary-background-color, #f5f5f5); }
        .sort-btn.active { border-color: var(--primary-color, #03a9f4); color: var(--primary-color, #03a9f4); background: rgba(3,169,244,0.08); font-weight: 600; }
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
        .btn-skip-confirm { background: var(--secondary-text-color, #757575); color: white; }
        .btn-skip-confirm:hover { filter: brightness(1.1); }
        .toggle-skipped-btn { background: none; border: 1px solid var(--divider-color, #e0e0e0); color: var(--secondary-text-color); border-radius: 4px; padding: 6px 14px; cursor: pointer; font-size: 0.875rem; }
        .toggle-skipped-btn:hover { background: var(--secondary-background-color, #f5f5f5); }
        .toggle-skipped-btn.active { border-color: var(--primary-color, #03a9f4); color: var(--primary-color, #03a9f4); }
        .loading, .error { text-align: center; padding: 48px; color: var(--secondary-text-color); }
        .error { color: var(--error-color, #db4437); }
        .history-info { padding: 10px 16px; background: var(--secondary-background-color, #f5f5f5); border-radius: 6px; font-size: 0.8rem; color: var(--secondary-text-color); margin-bottom: 16px; line-height: 1.5; }
        .group { margin-bottom: 24px; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.1); background: var(--card-background-color, white); }
        .group-header { display: flex; align-items: center; gap: 10px; padding: 12px 16px; background: var(--secondary-background-color, #f5f5f5); }
        .group-badge { color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
        .group-count { font-size: 0.875rem; color: var(--secondary-text-color); }
        .update-table { width: 100%; border-collapse: collapse; }
        .update-table th { text-align: left; padding: 10px 16px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--secondary-text-color); border-bottom: 1px solid var(--divider-color, #e0e0e0); }
        .update-table td { padding: 12px 16px; border-bottom: 1px solid var(--divider-color, #e0e0e0); vertical-align: middle; }
        .update-row:last-child td, tr:last-child td { border-bottom: none; }
        .update-row.installing { opacity: 0.85; }
        .installing-state { display: flex; flex-direction: column; gap: 6px; }
        .progress-bar { position: relative; height: 3px; min-width: 100px; background: var(--divider-color, #e0e0e0); border-radius: 2px; overflow: hidden; }
        .progress-bar::after { content: ''; position: absolute; top: 0; left: 0; height: 100%; width: 40%; background: var(--primary-color, #03a9f4); border-radius: 2px; animation: aum-progress 1.5s ease-in-out infinite; }
        @keyframes aum-progress { 0% { transform: translateX(-200%); } 100% { transform: translateX(350%); } }
        .type-chip { display: inline-block; color: white; padding: 1px 7px; border-radius: 10px; font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; margin-right: 8px; vertical-align: middle; }
        .not-installable-chip { display: inline-block; color: white; background: #e53935; padding: 1px 7px; border-radius: 10px; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.4px; margin-left: 8px; vertical-align: middle; cursor: default; }
        .not-installable-row td { opacity: 0.8; }
        .not-installable-action { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .not-installable-reason { font-size: 0.8rem; color: #e53935; font-weight: 500; white-space: nowrap; }
        .title { font-weight: 500; color: var(--primary-text-color); vertical-align: middle; }
        .version-from { color: var(--secondary-text-color); font-size: 0.875rem; }
        .arrow { margin: 0 6px; color: var(--secondary-text-color); }
        .version-to { color: var(--primary-color, #03a9f4); font-weight: 500; font-size: 0.875rem; }
        .date-cell { font-size: 0.875rem; color: var(--secondary-text-color); white-space: nowrap; }
        .release-link { margin-left: 4px; text-decoration: none; color: var(--primary-color, #03a9f4); }
        .major-badge { display: inline-block; margin-left: 8px; padding: 1px 6px; border-radius: 10px; font-size: 0.7rem; font-weight: 600; background: var(--warning-color, #ff9800); color: white; vertical-align: middle; cursor: default; }
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
        @media (max-width: 640px) {
          .update-table thead { display: none; }
          .update-table, .update-table tbody, .update-row { display: block; }
          .update-table td { display: block; padding: 4px 16px; border: none; }
          .update-row { border-bottom: 3px solid var(--divider-color, #e0e0e0); padding: 20px 0; }
          .update-row:last-child { border-bottom: none; }
          .update-row.installing { opacity: 0.85; }
          .action-cell { padding-top: 12px; white-space: normal; display: block; }
          .action-cell .btn { display: block; margin-right: 0; margin-bottom: 10px; width: 100%; text-align: center; box-sizing: border-box; padding: 12px; font-size: 0.95rem; }
          .action-cell .btn:last-child { margin-bottom: 0; }
          .progress-bar { min-width: 0; width: 100%; }
          .restart-banner { flex-wrap: wrap; }
          .header-actions { width: 100%; justify-content: flex-start; }
          .tab-bar { overflow-x: auto; }
        }
        .confirm-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000; display: flex; align-items: center; justify-content: center; }
        .confirm-dialog { background: var(--card-background-color, white); border-radius: 8px; padding: 24px; max-width: 420px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }
        .confirm-title { margin: 0 0 12px; font-size: 1.1rem; font-weight: 500; color: var(--primary-text-color); }
        .confirm-body { margin: 0 0 16px; color: var(--secondary-text-color); line-height: 1.5; }
        .confirm-actions { display: flex; gap: 8px; justify-content: flex-end; }
      </style>
      <div class="header">
        <h1>${this._tr("panel_title", "Update Manager")}</h1>
        <div class="header-actions">
          ${isPending ? this._renderSortButtons() : ""}
          <button class="ha-update-btn" onclick="this.getRootNode().host._navigateToHaUpdates()">${this._tr("ha_updates_btn", "HA Updates ↗")}</button>
          ${isPending ? `<button class="toggle-skipped-btn${this._showSkipped ? " active" : ""}" onclick="this.getRootNode().host._toggleSkipped()">${this._showSkipped ? this._tr("hide_skipped_btn", "Hide skipped") : this._tr("show_skipped_btn", "Show skipped")}</button>` : ""}
          <button class="refresh-btn" onclick="this.getRootNode().host._refresh()">${this._tr("refresh_btn", "Refresh list")}</button>
        </div>
      </div>
      <div class="tab-bar">
        <button class="tab-btn${this._view === "pending" ? " active" : ""}" onclick="this.getRootNode().host._setView('pending')">${this._tr("tab_pending", "Pending updates")}</button>
        <button class="tab-btn${this._view === "installed" ? " active" : ""}" onclick="this.getRootNode().host._setView('installed')">${this._tr("tab_installed", "Currently installed")}</button>
        <button class="tab-btn${this._view === "history" ? " active" : ""}" onclick="this.getRootNode().host._setView('history')">${this._tr("tab_history", "Latest installed")}</button>
      </div>
      ${isPending ? this._renderRestartBanner() : ""}
      ${isPending
        ? (this._loading
            ? `<div class="loading">${this._tr("loading", "Fetching updates…")}</div>`
            : this._error
              ? `<div class="error">${this._error}</div>`
              : this._renderUpdates())
        : ""}
      ${isPending ? this._renderSkippedSection() : ""}
      ${this._view === "installed" ? this._renderInstalled() : ""}
      ${this._view === "history" ? this._renderHistory() : ""}
      ${this._renderConfirmModal()}
    `;
  }
}

customElements.define("advanced-update-manager-panel", AdvancedUpdateManagerPanel);
