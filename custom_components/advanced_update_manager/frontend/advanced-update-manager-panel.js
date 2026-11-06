/**
 * Advanced Update Manager — Custom Panel
 * Displays all pending HA updates with type, release date, and action buttons.
 */
// This script is itself loaded with a `?v=<manifest version>` cache-buster
// (see js_url in __init__.py). Reusing that same query string on the
// translations fetch below keeps the two in sync — otherwise the browser
// can serve a stale cached translations/*.json even right after fetching a
// brand-new version of this very file.
const _SCRIPT_VERSION_QUERY = (() => {
  try {
    return new URL(document.currentScript?.src || "", window.location.href).search;
  } catch {
    return "";
  }
})();

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
    this._hostRebootRequired = false;
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
    // Feeling Lucky
    this._flStatus = { opcon: "ok", last_error: null, since: null, backup_taken: null, queue: { steps: [], current_item: null, started_at: null } };
    this._flActive = false;
    this._flSelected = new Set();
    this._flInfoCollapsed = false;
    try {
      this._flInfoCollapsed = localStorage.getItem("aum_fl_info_collapsed") === "1";
    } catch { /* localStorage unavailable, keep default */ }
    this._flConfirm = null;  // { backup: boolean }
    this._flDangerAck = false;
    this._flBusy = false;
    // Autopilot
    this._ap = null;          // snapshot from get_autopilot
    this._apDraft = null;     // editable copy of the config
    this._apDirty = false;
    this._apSaving = false;
    this._apLoading = false;
    this._apFilter = { addon: "", hacs: "" };
    this._apRefreshTimer = null;
    this._apPreview = null;   // evaluation of the unsaved draft
    this._apPreviewTimer = null;
    this._apRaw = {};         // raw text of number fields while typing
    this._apTargetsOpen = false;
    this._apTargetSearch = "";
    this._apTesting = false;
    this._apTestResults = {}; // target id -> { ok, error }
    this._apAdvOpen = false;
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
    await Promise.all([
      this._fetchUpdates(),
      this._fetchRestartInfo(),
      this._loadConfig(),
      this._fetchFeelingLuckyStatus(),
    ]);
    await this._subscribeStateChanges();
  }

  async _fetchFeelingLuckyStatus() {
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/get_feeling_lucky_status",
      });
      this._flStatus = Object.assign(
        { opcon: "ok", last_error: null, since: null, backup_taken: null, queue: { steps: [], current_item: null, started_at: null } },
        result,
      );
    } catch (e) {
      console.error("[AdvancedUpdateManager] feeling lucky status fetch failed", e);
    }
    this._render();
  }

  async _loadTranslations() {
    const lang = this._hass.locale?.language ?? "en";
    const base = "/advanced_update_manager_panel/translations/";
    try {
      const res = await fetch(`${base}${lang}.json${_SCRIPT_VERSION_QUERY}`);
      if (!res.ok) throw new Error("not found");
      this._t = await res.json();
    } catch {
      try {
        const res = await fetch(`${base}en.json${_SCRIPT_VERSION_QUERY}`);
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
      this._config = { default_backup_type: "full", feeling_lucky_enabled: false };
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
      this._hostRebootRequired = result.host_reboot_required || false;
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
    } else if (view === "autopilot") {
      await this._fetchAutopilot();
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
    } else if (this._view === "autopilot") {
      await this._fetchAutopilot();
    }
  }

  async _subscribeStateChanges() {
    // subscribeEvents is async and resolves to the unsubscribe function —
    // it must be awaited, otherwise this._unsubscribe ends up holding the
    // pending Promise instead of a callable, and disconnectedCallback's
    // this._unsubscribe() throws "not a function".
    this._unsubscribe = await this._hass.connection.subscribeEvents((event) => {
      const entityId = event.data?.entity_id;
      if (!entityId) return;

      if (entityId.includes("autopilot")) {
        this._apScheduleRefresh();
        return;
      }

      if (entityId.includes("opcon") || entityId.includes("feeling_lucky_queue")) {
        this._apScheduleRefresh();
        // Also refresh restart info: a Feeling Lucky run waiting on a HAOS
        // reboot needs the restart banner (with its "Reboot Host" button) to
        // show up promptly, not just whenever something else triggers a
        // refresh — that's the manual fallback if the automatic
        // hassio.host_reboot attempt doesn't visibly take effect.
        this._fetchFeelingLuckyStatus();
        this._fetchRestartInfo();
        return;
      }

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
    // A Supervisor "reboot required" repair (e.g. after a HAOS update) needs
    // hassio.host_reboot — homeassistant.restart only restarts Core and
    // would leave the actual repair in place.
    const useHostReboot = this._hostRebootRequired;
    try {
      await this._hass.connection.sendMessagePromise({
        type: "call_service",
        domain: useHostReboot ? "hassio" : "homeassistant",
        service: useHostReboot ? "host_reboot" : "restart",
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
      if (e?.code === "backup_failed") {
        alert(`${this._tr("backup_failed_alert", "The backup failed, so nothing was updated.")}\n\n${e.message || ""}`);
      }
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

  // ── Feeling Lucky ────────────────────────────────────────────────────────

  static FL_PHASE_ORDER = { supervisor: 0, hacs: 1, addon: 2, device: 3, haos: 4, core: 5 };

  _toggleFeelingLucky() {
    this._flActive = !this._flActive;
    if (this._flActive) {
      // Always reset to default on open — never remember the last selection.
      this._flSelected = new Set(
        this._updates.filter((u) => u.type !== "device").map((u) => u.entity_id)
      );
    }
    this._render();
  }

  _toggleFlSelected(entityId) {
    if (this._flSelected.has(entityId)) this._flSelected.delete(entityId);
    else this._flSelected.add(entityId);
    this._render();
  }

  _toggleFlInfo() {
    this._flInfoCollapsed = !this._flInfoCollapsed;
    try {
      localStorage.setItem("aum_fl_info_collapsed", this._flInfoCollapsed ? "1" : "0");
    } catch { /* localStorage unavailable, just keep the in-memory value */ }
    this._render();
  }

  _flResolvedSteps() {
    const order = AdvancedUpdateManagerPanel.FL_PHASE_ORDER;
    return this._updates
      .filter((u) => this._flSelected.has(u.entity_id) && Object.hasOwn(order, u.type))
      .sort((a, b) => order[a.type] - order[b.type]);
  }

  _requestFeelingLucky(backup) {
    this._flDangerAck = false;
    this._flConfirm = { backup };
    this._render();
  }

  _cancelFlConfirm() {
    this._flConfirm = null;
    this._render();
  }

  _toggleFlDangerAck() {
    this._flDangerAck = !this._flDangerAck;
    this._render();
  }

  async _doFeelingLucky() {
    const { backup } = this._flConfirm;
    const entityIds = this._flResolvedSteps().map((u) => u.entity_id);
    this._flConfirm = null;
    this._flActive = false;
    this._flBusy = true;
    this._render();
    try {
      await this._hass.connection.sendMessagePromise({
        type: "call_service",
        domain: "advanced_update_manager",
        service: "start_feeling_lucky",
        service_data: { entity_id: entityIds, backup },
      });
    } catch (e) {
      console.error("[AdvancedUpdateManager] start_feeling_lucky failed", e);
    }
    this._flBusy = false;
    await this._fetchFeelingLuckyStatus();
  }

  async _resetOpcon() {
    try {
      await this._hass.connection.sendMessagePromise({
        type: "call_service",
        domain: "advanced_update_manager",
        service: "reset_opcon",
        service_data: {},
      });
    } catch (e) {
      console.error("[AdvancedUpdateManager] reset_opcon failed", e);
    }
    await this._fetchFeelingLuckyStatus();
  }

  _typeLabel(type) {
    return { supervisor: "Supervisor", core: "Core", haos: "HA OS", addon: "Apps", hacs: "HACS", device: "Device", other: "Other" }[type] || type;
  }

  _typeColor(type) {
    return { supervisor: "#795548", core: "#03a9f4", haos: "#4caf50", addon: "#ff9800", hacs: "#9c27b0", device: "#607d8b", other: "#9e9e9e" }[type] || "#9e9e9e";
  }

  _renderRestartBanner() {
    if (!this._restartRequired) return "";
    const isAdmin = this._hass.user?.is_admin ?? false;
    const text = this._hostRebootRequired
      ? this._tr("host_reboot_banner", "The host needs to be rebooted for a recent OS update to take effect.")
      : this._tr("restart_banner", "Home Assistant needs to be restarted for recent changes to take effect.");
    const btnLabel = this._hostRebootRequired
      ? this._tr("host_reboot_btn", "Reboot Host")
      : this._tr("restart_btn", "Restart Home Assistant");
    return `
      <div class="restart-banner">
        <span class="restart-icon">⚠</span>
        <span class="restart-text">${text}</span>
        ${isAdmin
          ? `<button class="btn btn-restart" onclick="this.getRootNode().host._requestRestart()">${btnLabel}</button>`
          : ""}
      </div>`;
  }

  _renderUpdateRow(u, showTypeBadge = false, flMode = false) {
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

    const flEligible = flMode && Object.hasOwn(AdvancedUpdateManagerPanel.FL_PHASE_ORDER, u.type);
    const flCell = flMode
      ? flEligible
        ? `<td class="fl-checkbox-cell"><input type="checkbox" ${this._flSelected.has(u.entity_id) ? "checked" : ""} onchange="this.getRootNode().host._toggleFlSelected('${this._escHtml(u.entity_id)}')"></td>`
        : `<td class="fl-checkbox-cell"><span class="fl-not-eligible" title="${this._tr("fl_not_eligible", "Not eligible for Feeling Lucky")}">—</span></td>`
      : "";

    return `
      <tr class="update-row${isInstalling ? " installing" : ""}${isNotInstallable ? " not-installable-row" : ""}">
        ${flCell}
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
    const typeOrder = ["supervisor", "core", "haos", "addon", "hacs", "device", "other"];
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
                ${this._flActive ? `<th class="fl-th"></th>` : ""}
                <th>${this._tr("col_name", "Name")}</th>
                <th>${this._tr("col_version", "Version")}</th>
                <th>${this._tr("col_release_date", "Release date")}</th>
                <th>${this._tr("col_action", "Action")}</th>
              </tr>
            </thead>
            <tbody>
              ${groups[type].map((u) => this._renderUpdateRow(u, false, this._flActive)).join("")}
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
              ${this._flActive ? `<th class="fl-th"></th>` : ""}
              <th>${this._tr("col_name", "Name")}</th>
              <th>${this._tr("col_version", "Version")}</th>
              <th>${this._tr("col_release_date", "Release date")}</th>
              <th>${this._tr("col_action", "Action")}</th>
            </tr>
          </thead>
          <tbody>
            ${sorted.map((u) => this._renderUpdateRow(u, true, this._flActive)).join("")}
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

    const typeOrder = { supervisor: 0, core: 1, haos: 2, addon: 3, hacs: 4, device: 5, other: 6 };
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
      const title = this._hostRebootRequired
        ? this._tr("confirm_host_reboot_title", "Confirm host reboot")
        : this._tr("confirm_restart_title", "Confirm restart");
      const body = this._hostRebootRequired
        ? this._tr("confirm_host_reboot_body", "Are you sure you want to reboot the host? This will cause a brief outage while the whole system restarts.")
        : this._tr("confirm_restart_body", "Are you sure you want to restart Home Assistant? This will cause a brief outage.");
      const btnLabel = this._hostRebootRequired
        ? this._tr("host_reboot_btn", "Reboot Host")
        : this._tr("btn_restart", "Restart");
      return `
        <div class="confirm-overlay">
          <div class="confirm-dialog">
            <p class="confirm-title">${title}</p>
            <p class="confirm-body">${body}</p>
            <div class="confirm-actions">
              <button class="btn btn-skip" onclick="this.getRootNode().host._cancelConfirm()">${this._tr("btn_cancel", "Cancel")}</button>
              <button class="btn btn-restart-confirm" onclick="this.getRootNode().host._doRestart()">${btnLabel}</button>
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

    // "App backup" = Supervisor's partial backup of that one add-on, made by
    // update.install(backup: true). It only exists for apps (add-ons) — for
    // Core, OS, HACS, firmware etc. the only meaningful choice is a full backup.
    const isApp = updateType === "addon";
    return `
      <div class="confirm-overlay">
        <div class="confirm-dialog">
          <p class="confirm-title">${this._tr("confirm_title", "Confirm update")}</p>
          <p class="confirm-body">${this._tr("confirm_body_backup", "Do you want to back up and update")} <strong>${this._escHtml(title)}</strong>?</p>
          <div class="confirm-actions">
            <button class="btn btn-skip" onclick="this.getRootNode().host._cancelConfirm()">${this._tr("btn_cancel", "Cancel")}</button>
            ${isApp ? `<button class="btn btn-backup" onclick="this.getRootNode().host._doInstall('addon_only')">${this._tr("btn_addon_backup_install", "App backup + Install")}</button>` : ""}
            <button class="btn btn-backup" onclick="this.getRootNode().host._doInstall('full')">${this._tr("btn_full_backup_install", "Full backup + Install")}</button>
          </div>
        </div>
      </div>`;
  }

  _renderFeelingLuckyBanner() {
    const opcon = this._flStatus.opcon;
    if (opcon === "running") {
      const queue = this._flStatus.queue || {};
      const steps = queue.steps || [];
      const doneCount = steps.filter((s) => s.status === "done").length;
      const current = steps.find((s) => s.entity_id === queue.current_item);
      return `
        <div class="fl-banner fl-banner-running">
          <span class="fl-banner-icon">🔵</span>
          <span class="fl-banner-text">
            ${this._tr("fl_running", "Feeling Lucky is running")} — ${doneCount}/${steps.length}
            ${current ? `: <strong>${this._escHtml(current.title)}</strong>` : ""}
          </span>
        </div>`;
    }
    if (opcon === "critical") {
      const err = this._flStatus.last_error || this._tr("fl_unknown_error", "Unknown error");
      return `
        <div class="fl-banner fl-banner-critical">
          <span class="fl-banner-icon">🔴</span>
          <span class="fl-banner-text"><strong>Code Red</strong> — ${this._escHtml(err)}</span>
          <button class="btn fl-reset-btn" onclick="this.getRootNode().host._resetOpcon()">${this._tr("fl_reset_btn", "Reset OPCON")}</button>
        </div>`;
    }
    return "";
  }

  _renderFeelingLuckyPanel() {
    if (!this._flActive) return "";
    const blocked = this._flStatus.opcon === "critical";
    const blockedTitle = blocked ? this._tr("fl_blocked_tooltip", "OPCON is Code Red — reset it first") : "";
    const infoBody = this._flInfoCollapsed ? "" : `
      <div class="fl-info-body">
        <p class="fl-order">Supervisor → HACS → Add-ons → ${this._tr("fl_device_firmware", "Device firmware")} → OS → Core</p>
        <p class="fl-info-hint">${this._tr(
          "fl_info_hint",
          "Supervisor always goes first — the platform itself requires it. Everything else runs lowest-risk first, saving the restart-heavy OS and Core updates for last, once HACS/add-ons/firmware are already confirmed healthy. Device firmware is excluded by default below — check it in manually if you want it included."
        )}</p>
      </div>`;
    return `
      <div class="fl-panel">
        <div class="fl-info-box">
          <button class="fl-info-toggle" onclick="this.getRootNode().host._toggleFlInfo()">
            ⓘ ${this._tr("fl_info_title", "Why this order?")} <span class="fl-info-arrow">${this._flInfoCollapsed ? "▸" : "▾"}</span>
          </button>
          ${infoBody}
        </div>
        <div class="fl-commit-row">
          <button class="btn fl-btn-day" ${blocked ? "disabled" : ""} title="${blockedTitle}" onclick="this.getRootNode().host._requestFeelingLucky(true)">
            ${this._tr("fl_btn_day", "Make my day – Install all selected with a Full backup")}
          </button>
          <button class="btn fl-btn-crash" ${blocked ? "disabled" : ""} title="${blockedTitle}" onclick="this.getRootNode().host._requestFeelingLucky(false)">
            ${this._tr("fl_btn_crash", "Make my crash — Install all selected, but No backup (Danger mode)")}
          </button>
        </div>
      </div>`;
  }

  _renderFlConfirmModal() {
    if (!this._flConfirm) return "";
    const { backup } = this._flConfirm;
    const steps = this._flResolvedSteps();

    let orderHtml = "";
    let lastPhase = null;
    for (const s of steps) {
      if (s.type !== lastPhase) {
        if (lastPhase !== null) orderHtml += `</ul></div>`;
        orderHtml += `<div class="fl-confirm-group"><div class="fl-confirm-phase">${this._typeLabel(s.type)}</div><ul>`;
        lastPhase = s.type;
      }
      orderHtml += `<li>${this._escHtml(s.title)}</li>`;
    }
    if (lastPhase !== null) orderHtml += `</ul></div>`;
    if (!orderHtml) orderHtml = `<p>${this._tr("fl_confirm_none", "Nothing eligible is selected.")}</p>`;

    const canConfirm = backup || this._flDangerAck;
    const danger = !backup ? `
      <label class="fl-danger-ack">
        <input type="checkbox" ${this._flDangerAck ? "checked" : ""} onchange="this.getRootNode().host._toggleFlDangerAck()">
        ${this._tr("fl_danger_ack", "I understand no backup is taken before this run.")}
      </label>` : "";

    return `
      <div class="confirm-overlay">
        <div class="confirm-dialog fl-confirm-dialog">
          <p class="confirm-title">${backup ? this._tr("fl_confirm_title_day", "Make my day") : this._tr("fl_confirm_title_crash", "Make my crash")}</p>
          <p class="confirm-body">${this._tr("fl_confirm_body", "This will run, in this order:")}</p>
          <div class="fl-confirm-steps">${orderHtml}</div>
          ${danger}
          <div class="confirm-actions">
            <button class="btn btn-skip" onclick="this.getRootNode().host._cancelFlConfirm()">${this._tr("btn_cancel", "Cancel")}</button>
            <button class="btn ${backup ? "fl-btn-day" : "fl-btn-crash"}" ${canConfirm ? "" : "disabled"} onclick="this.getRootNode().host._doFeelingLucky()">
              ${backup ? this._tr("fl_btn_day_confirm", "Make my day") : this._tr("fl_btn_crash_confirm", "Make my crash")}
            </button>
          </div>
        </div>
      </div>`;
  }

  _escHtml(str) {
    return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ── Autopilot ────────────────────────────────────────────────────────────

  async _fetchAutopilot() {
    this._apLoading = !this._ap;
    if (this._apLoading) this._render();
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/get_autopilot",
      });
      this._ap = result;
      if (!this._apDirty) this._apDraft = JSON.parse(JSON.stringify(result.config));
    } catch (e) {
      console.error("[AdvancedUpdateManager] autopilot fetch failed", e);
    }
    this._apLoading = false;
    this._render();
  }

  _apScheduleRefresh() {
    if (this._view !== "autopilot") return;
    clearTimeout(this._apRefreshTimer);
    this._apRefreshTimer = setTimeout(() => this._fetchAutopilot(), 400);
  }

  _apSet(key, value) {
    this._apDraft[key] = value;
    this._apDirty = true;
    this._render();
    this._apSchedulePreview();
  }

  // Live preview of UNSAVED settings — evaluated server-side with the exact
  // rules a real run uses, debounced so typing doesn't spam the backend.
  _apSchedulePreview() {
    clearTimeout(this._apPreviewTimer);
    this._apPreviewTimer = setTimeout(async () => {
      try {
        const result = await this._hass.connection.sendMessagePromise({
          type: "advanced_update_manager/autopilot_preview",
          config: this._apDraft,
        });
        if (this._apDirty) {
          this._apPreview = result.evaluation;
          this._render();
        }
      } catch (e) {
        console.error("[AdvancedUpdateManager] autopilot preview failed", e);
      }
    }, 250);
  }

  // Number fields: update the draft on every keystroke (so Save shows up at
  // once), keep the raw text while typing, clamp when the field is left.
  _apNumInput(key, raw, min, max) {
    this._apRaw[key] = raw;
    const n = parseInt(raw, 10);
    if (!Number.isNaN(n)) {
      this._apDraft[key] = Math.max(min, Math.min(max, n));
    }
    this._apDirty = true;
    this._render();
    this._apSchedulePreview();
  }

  _apNumCommit(key, min, max) {
    const raw = this._apRaw[key];
    delete this._apRaw[key];
    const n = parseInt(this._apDraft[key], 10);
    this._apDraft[key] = Number.isNaN(n) ? min : Math.max(min, Math.min(max, n));
    // Only re-render if the field shows something other than the final value
    // (empty, out of range, "07"). Re-rendering here otherwise would swap out
    // the Save button under the mouse when the user clicks it straight from
    // the field — the blur fires first and the click would be lost.
    if (raw !== undefined && raw !== String(this._apDraft[key])) {
      this._render();
      this._apSchedulePreview();
    }
  }

  _apSetMode(type, mode) {
    const prev = this._apDraft[`${type}_mode`];
    // "All except" stores exclusions, "Only selected" stores inclusions —
    // the same list means opposite things, so start fresh when flipping.
    if ((prev === "all_except" && mode === "only") || (prev === "only" && mode === "all_except")) {
      this._apDraft[`${type}_selected`] = [];
    }
    this._apSet(`${type}_mode`, mode);
  }

  _apToggleSelected(type, id) {
    const key = `${type}_selected`;
    const set = new Set(this._apDraft[key] || []);
    if (set.has(id)) set.delete(id); else set.add(id);
    this._apSet(key, [...set]);
  }

  _apStepTime(part, delta) {
    let [h, m] = String(this._apDraft.time || "03:00").split(":").map((x) => parseInt(x, 10) || 0);
    if (part === "h") {
      h = (h + delta + 24) % 24;
    } else {
      // Snap to the 5-minute grid first, so 03:07 ▲ -> 03:10, ▼ -> 03:05.
      const snapped = delta > 0 ? Math.floor(m / 5) * 5 : Math.ceil(m / 5) * 5;
      m = (snapped + delta + 60) % 60;
    }
    this._apSet("time", `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
  }

  _apToggleWeekday(day) {
    const set = new Set(this._apDraft.weekdays || []);
    if (set.has(day)) set.delete(day); else set.add(day);
    this._apSet("weekdays", [...set].sort());
  }

  _apToggleTarget(target) {
    const set = new Set(this._apDraft.notify_targets || []);
    if (set.has(target)) set.delete(target); else set.add(target);
    this._apTestResults = {};
    this._apSet("notify_targets", [...set]);
  }

  _apToggleTargetMenu() {
    this._apTargetsOpen = !this._apTargetsOpen;
    this._apTargetSearch = "";
    this._render();
  }

  _apSetTargetSearch(value) {
    this._apTargetSearch = value;
    this._render();
  }

  async _apSendTest() {
    const targets = this._apDraft.notify_targets || [];
    if (!targets.length) return;
    this._apTesting = true;
    this._apTestResults = {};
    this._render();
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/autopilot_test_notify",
        targets,
      });
      for (const r of res.results || []) this._apTestResults[r.target] = r;
    } catch (e) {
      console.error("[AdvancedUpdateManager] test notification failed", e);
      for (const t of targets) this._apTestResults[t] = { ok: false, error: e.message || String(e) };
    }
    this._apTesting = false;
    this._render();
  }

  _apTargetName(id) {
    const t = (this._ap.available_targets || []).find((x) => x.id === id);
    if (t) return t.name;
    return id.replace(/^entity:/, "");
  }

  _renderApTargets() {
    const selected = this._apDraft.notify_targets || [];
    const available = this._ap.available_targets || [];
    const icon = { builtin: "🔔", mobile: "📱", other: "💬", advanced: "⚙️" };
    const groupOf = (id) => (available.find((x) => x.id === id) || {}).group;

    const chips = selected.map((id) => {
      const known = available.some((x) => x.id === id);
      const res = this._apTestResults[id];
      const cls = !known ? " ap-chip-missing" : res ? (res.ok ? " ap-chip-ok" : " ap-chip-fail") : "";
      const mark = res ? (res.ok ? " ✓" : " ✗") : "";
      const title = !known
        ? this._tr("ap_target_missing", "This receiver no longer exists")
        : res && !res.ok ? res.error || "" : id;
      return `
        <span class="ap-chip${cls}" title="${this._escHtml(title)}">
          ${icon[groupOf(id)] || "❔"} ${this._escHtml(this._apTargetName(id))}${mark}
          <button class="ap-chip-x" title="${this._tr("ap_remove", "Remove")}" onclick="this.getRootNode().host._apToggleTarget(${this._escHtml(JSON.stringify(id))})">✕</button>
        </span>`;
    }).join("");

    let menu = "";
    if (this._apTargetsOpen) {
      const q = (this._apTargetSearch || "").toLowerCase();
      const choices = available.filter((x) => !selected.includes(x.id)
        && (!q || x.name.toLowerCase().includes(q) || x.id.toLowerCase().includes(q)));
      const groups = [
        ["builtin", this._tr("ap_grp_builtin", "Home Assistant")],
        ["mobile", this._tr("ap_grp_mobile", "Phones")],
        ["other", this._tr("ap_grp_other", "Other")],
        ["advanced", this._tr("ap_grp_advanced", "Advanced")],
      ];
      const body = groups.map(([g, label]) => {
        const items = choices.filter((x) => x.group === g);
        if (!items.length) return "";
        return `
          <div class="ap-menu-group">${icon[g]} ${label}</div>
          ${items.map((x) => `
            <button class="ap-menu-item" onclick="this.getRootNode().host._apToggleTarget(${this._escHtml(JSON.stringify(x.id))})">
              <span>${this._escHtml(x.name)}</span>
              ${x.name !== x.id ? `<span class="ap-menu-id">${this._escHtml(x.id.replace(/^entity:/, ""))}</span>` : ""}
            </button>`).join("")}`;
      }).join("");
      const noPhones = !available.some((x) => x.group === "mobile");
      menu = `
        <div class="ap-menu">
          <input class="search-input ap-menu-search" data-focus="ap_target_search" placeholder="${this._tr("ap_search", "Search…")}" value="${this._escHtml(this._apTargetSearch || "")}" oninput="this.getRootNode().host._apSetTargetSearch(this.value)">
          <div class="ap-menu-list">${body || `<div class="ap-muted">${this._tr("ap_no_more_targets", "No more receivers.")}</div>`}</div>
          ${noPhones ? `<div class="ap-hint">📱 ${this._tr("ap_no_phones", "No phones found. Install the Home Assistant Companion app on your phone and log in — it shows up here automatically.")}</div>` : ""}
          <div class="ap-menu-foot"><button class="btn btn-skip" onclick="this.getRootNode().host._apToggleTargetMenu()">${this._tr("ap_done", "Done")}</button></div>
        </div>`;
    }

    const hasSkipCapable = selected.some((id) => groupOf(id) === "mobile");
    const failed = Object.values(this._apTestResults).filter((r) => !r.ok);
    return `
      <div class="ap-chips">
        ${chips || `<span class="ap-muted">${this._tr("ap_no_targets", "No receivers — you will only see results in this panel.")}</span>`}
        <button class="ap-add" onclick="this.getRootNode().host._apToggleTargetMenu()">+ ${this._tr("ap_add_target", "Add receiver")} ${this._apTargetsOpen ? "▴" : "▾"}</button>
      </div>
      ${menu}
      <div class="ap-test-row">
        <button class="btn btn-backup" ${!selected.length || this._apTesting ? "disabled" : ""} onclick="this.getRootNode().host._apSendTest()">
          ${this._apTesting ? this._tr("ap_testing", "Sending…") : "📨 " + this._tr("ap_send_test", "Send test notification")}
        </button>
        ${failed.length ? `<span class="ap-err">${this._tr("ap_test_failed", "Did not arrive at")}: ${failed.map((r) => this._escHtml(this._apTargetName(r.target)) + (r.error ? ` (${this._escHtml(r.error)})` : "")).join(", ")}</span>`
          : Object.keys(this._apTestResults).length ? `<span class="ap-ok">✓ ${this._tr("ap_test_ok", "Sent — check that it arrived.")}</span>` : ""}
      </div>
      ${selected.length && !hasSkipCapable && this._apDraft.notify_events?.prewarn ? `<div class="ap-hint">${this._tr("ap_skip_needs_phone", "The \"Skip tonight\" button only appears on phones (Companion app).")}</div>` : ""}`;
  }

  _renderApAdvanced() {
    const yaml = `alias: Autopilot to my own receiver
triggers:
  - trigger: event
    event_type: advanced_update_manager_autopilot
conditions:
  # only events that carry a ready-made text
  - condition: template
    value_template: "{{ trigger.event.data.message is defined }}"
actions:
  - action: notify.send_message
    target:
      entity_id: notify.my_receiver   # change this
    data:
      title: "{{ trigger.event.data.title }}"
      message: "{{ trigger.event.data.message }}"`;
    return `
      <div class="fl-info-box ap-adv">
        <button class="fl-info-toggle" onclick="this.getRootNode().host._apToggleAdv()">
          ⓘ ${this._tr("ap_adv_title", "Advanced: your own automations")} <span class="fl-info-arrow">${this._apAdvOpen ? "▾" : "▸"}</span>
        </button>
        ${this._apAdvOpen ? `
          <div class="fl-info-body">
            <p class="fl-info-hint">${this._tr("ap_adv_body", "Every Autopilot step is fired as the event advanced_update_manager_autopilot — whatever you ticked above. Use it to announce on a speaker, flash a lamp, post to a chat… The field \"event\" tells what happened: prewarn, start, backup_ok, backup_failed, update_failed, summary, restarted, nothing_to_do, new_components, and when a run ends: success, failed, skipped, blocked.")}</p>
            <pre class="ap-yaml">${this._escHtml(yaml)}</pre>
          </div>` : ""}
      </div>`;
  }

  _apToggleAdv() {
    this._apAdvOpen = !this._apAdvOpen;
    this._render();
  }

  _apToggleEvent(event) {
    if (event === "backup_failed") return;  // locked on
    const events = { ...(this._apDraft.notify_events || {}) };
    events[event] = !events[event];
    this._apSet("notify_events", events);
  }

  _apSetFilter(type, value) {
    this._apFilter[type] = value;
    this._render();
    const input = this.shadowRoot.querySelector(`.ap-search[data-type="${type}"]`);
    if (input) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }

  async _apSave() {
    this._apSaving = true;
    this._render();
    try {
      const result = await this._hass.connection.sendMessagePromise({
        type: "advanced_update_manager/save_autopilot",
        config: this._apDraft,
      });
      this._ap = result;
      this._apDraft = JSON.parse(JSON.stringify(result.config));
      this._apDirty = false;
      this._apPreview = null;
      this._apRaw = {};
    } catch (e) {
      console.error("[AdvancedUpdateManager] autopilot save failed", e);
      alert(this._tr("ap_save_failed", "Could not save the Autopilot settings."));
    }
    this._apSaving = false;
    this._render();
  }

  _apDiscard() {
    this._apDraft = JSON.parse(JSON.stringify(this._ap.config));
    this._apDirty = false;
    this._apPreview = null;
    this._apRaw = {};
    this._render();
  }

  async _apRunNow() {
    if (!confirm(this._tr("ap_run_now_confirm", "Run Autopilot now? A full backup is taken first, then all ready updates are installed. Home Assistant may restart."))) return;
    await this._hass.connection.sendMessagePromise({ type: "advanced_update_manager/autopilot_run_now" });
    setTimeout(() => this._fetchAutopilot(), 800);
  }

  async _apSkipNext(skip) {
    await this._hass.connection.sendMessagePromise({ type: "advanced_update_manager/autopilot_skip_next", skip });
    await this._fetchAutopilot();
  }

  _apFormatDateTime(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString(this._hass.locale?.language || undefined, {
        weekday: "short", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  // "Released 2026-11-05 (1 d ago) · ready in 110 d (2027-02-24)" — the
  // ready date alone is easily mistaken for a release date.
  _apAgeText(u) {
    const basis = u.release_date
      ? `${this._tr("ap_released", "released")} ${this._escHtml(u.age_from)}`
      : `${this._tr("ap_first_seen", "first seen")} ${this._escHtml(u.age_from)}`;
    let left = "";
    if (u.eligible_from) {
      const ms = new Date(`${u.eligible_from}T00:00:00`) - new Date(new Date().toDateString());
      const days = Math.max(0, Math.round(ms / 86400000));
      left = ` · ${this._tr("ap_ready_in", "ready in {n} d").replace("{n}", days)} (${this._escHtml(u.eligible_from)})`;
    }
    return `${basis}${left}`;
  }

  _apReasonLabel(reason) {
    const fallback = {
      type_off: "Type is off",
      excluded: "Excluded",
      not_selected: "Not selected",
      not_installable: "Not installable",
      in_progress: "Installing",
      major_version: "Major version",
      too_new: "Too new",
    };
    return this._tr(`ap_reason_${reason}`, fallback[reason] || reason);
  }

  _apStatusLabel(status) {
    const fallback = {
      never_run: "Never run",
      running: "Running",
      success: "Success",
      failed: "Failed — Code Red",
      backup_failed: "Backup failed — nothing installed",
      nothing_to_do: "No updates to install",
      skipped: "Skipped",
      blocked: "Blocked",
    };
    return this._tr(`ap_status_${status}`, fallback[status] || status);
  }

  _renderApTypeBlock(type, label) {
    const d = this._apDraft;
    const mode = d[`${type}_mode`] || "off";
    const modes = [
      ["off", this._tr("ap_mode_off", "Off")],
      ["all", this._tr("ap_mode_all", "All")],
      ["all_except", this._tr("ap_mode_all_except", "All except…")],
      ["only", this._tr("ap_mode_only", "Only selected")],
    ];
    const modeHint = {
      off: this._tr("ap_mode_off_hint", "Nothing of this type is installed by Autopilot."),
      all: this._tr("ap_mode_all_hint", "Everything is included. Newly installed ones are included automatically."),
      all_except: this._tr("ap_mode_all_except_hint", "Tick the ones Autopilot should never touch. Newly installed ones are included automatically."),
      only: this._tr("ap_mode_only_hint", "Tick the ones Autopilot may update. Newly installed ones are NOT included until you tick them."),
    }[mode];

    let list = "";
    if (mode === "all_except" || mode === "only") {
      const selected = new Set(d[`${type}_selected`] || []);
      const filter = (this._apFilter[type] || "").toLowerCase();
      const comps = (this._ap.components || []).filter((c) => c.type === type);
      const shown = comps.filter((c) => !filter || String(c.title).toLowerCase().includes(filter));
      const newIds = new Set((this._ap.new_unscheduled || []).map((c) => c.id));
      const rows = shown.map((c) => {
        const checked = selected.has(c.id);
        const tag = mode === "all_except"
          ? (checked ? `<span class="ap-tag ap-tag-off">${this._tr("ap_tag_excluded", "excluded")}</span>` : "")
          : (checked ? `<span class="ap-tag ap-tag-on">${this._tr("ap_tag_included", "included")}</span>` : "");
        return `
          <label class="ap-comp">
            <input type="checkbox" ${checked ? "checked" : ""} onchange="this.getRootNode().host._apToggleSelected('${type}', ${this._escHtml(JSON.stringify(c.id))})">
            <span class="ap-comp-title">${this._escHtml(c.title)}</span>
            ${c.pending ? `<span class="ap-tag ap-tag-pending">${this._tr("ap_tag_pending", "update available")}</span>` : ""}
            ${c.is_self ? `<span class="ap-tag">${this._tr("ap_tag_self", "always last")}</span>` : ""}
            ${newIds.has(c.id) ? `<span class="ap-tag ap-tag-new">${this._tr("ap_tag_new", "new")}</span>` : ""}
            ${tag}
          </label>`;
      }).join("");
      list = `
        <div class="ap-comp-box">
          <div class="search-bar">
            <input class="search-input ap-search" data-type="${type}" placeholder="${this._tr("ap_search", "Search…")}" value="${this._escHtml(this._apFilter[type] || "")}" oninput="this.getRootNode().host._apSetFilter('${type}', this.value)">
            <span class="search-count">${selected.size} ${mode === "all_except" ? this._tr("ap_count_excluded", "excluded") : this._tr("ap_count_selected", "selected")} · ${comps.length} ${this._tr("ap_count_installed", "installed")}</span>
          </div>
          <div class="ap-comp-list">${rows || `<div class="ap-muted">${this._tr("ap_no_components", "Nothing installed of this type.")}</div>`}</div>
        </div>`;
    }

    return `
      <div class="ap-type">
        <div class="ap-type-head">
          <span class="type-chip" style="background:${this._typeColor(type)}">${label}</span>
          <div class="ap-seg">
            ${modes.map(([m, l]) => `<button class="ap-seg-btn${mode === m ? " active" : ""}" onclick="this.getRootNode().host._apSetMode('${type}', '${m}')">${l}</button>`).join("")}
          </div>
        </div>
        <div class="ap-hint">${modeHint}</div>
        ${list}
      </div>`;
  }

  _renderApPreviewList(items, showReason) {
    if (!items.length) return `<div class="ap-muted">${this._tr("ap_none", "None")}</div>`;
    return `<ul class="ap-list">${items.map((u) => `
      <li>
        <span class="type-chip" style="background:${this._typeColor(u.type)}">${this._typeLabel(u.type)}</span>
        <span class="ap-li-title">${this._escHtml(u.title)}</span>
        <span class="version-from">${this._escHtml(u.from_version)}</span><span class="arrow">→</span><span class="version-to">${this._escHtml(u.to_version)}</span>
        ${u.major_version_change ? `<span class="major-badge">MAJOR</span>` : ""}
        ${showReason ? `<span class="ap-reason">${u.reason === "too_new" ? this._apAgeText(u) : this._apReasonLabel(u.reason)}</span>` : ""}
      </li>`).join("")}</ul>`;
  }

  _renderApLastRun() {
    const last = this._ap.state?.last_run;
    if (!last) return `<div class="ap-muted">${this._tr("ap_never_run", "Autopilot has not run yet.")}</div>`;
    const installed = (last.installed || []).map((i) => this._escHtml(i.title)).join(", ");
    const failed = (last.failed || []).map((i) => `${this._escHtml(i.title)}${i.error ? ` (${this._escHtml(i.error)})` : ""}`).join(", ");
    return `
      <div class="ap-kv"><span>${this._tr("ap_status", "Status")}</span><strong class="ap-status-${last.status}">${this._apStatusLabel(last.status)}</strong></div>
      <div class="ap-kv"><span>${this._tr("ap_finished", "Finished")}</span><span>${this._apFormatDateTime(last.finished_at)}</span></div>
      ${last.backup ? `<div class="ap-kv"><span>${this._tr("ap_backup", "Backup")}</span><span>${this._escHtml(last.backup)}</span></div>` : ""}
      ${installed ? `<div class="ap-kv"><span>${this._tr("ap_installed", "Installed")}</span><span>${installed}</span></div>` : ""}
      ${failed ? `<div class="ap-kv"><span>${this._tr("ap_failed", "Failed")}</span><span class="ap-err">${failed}</span></div>` : ""}
      ${(last.not_started || []).length ? `<div class="ap-kv"><span>${this._tr("ap_not_started", "Not started")}</span><span>${(last.not_started || []).map((t) => this._escHtml(t)).join(", ")}</span></div>` : ""}
      ${last.error && !failed ? `<div class="ap-kv"><span>${this._tr("ap_error", "Error")}</span><span class="ap-err">${this._escHtml(this._tr(`ap_err_${last.error}`, last.error))}</span></div>` : ""}
      ${last.restarted ? `<div class="ap-kv"><span></span><span>${this._tr("ap_restarted", "Home Assistant was restarted to finish HACS updates.")}</span></div>` : ""}`;
  }

  _renderAutopilot() {
    if (this._apLoading || !this._ap || !this._apDraft) {
      return `<div class="loading">${this._tr("loading", "Loading…")}</div>`;
    }
    const ap = this._ap;
    const d = this._apDraft;
    const ev = (this._apDirty && this._apPreview) || ap.evaluation || { ready: [], waiting: [], excluded: [] };
    const codeRed = ap.opcon === "critical";
    const status = ap.state?.status || "never_run";
    const days = [
      [0, this._tr("ap_mon", "Mon")], [1, this._tr("ap_tue", "Tue")], [2, this._tr("ap_wed", "Wed")],
      [3, this._tr("ap_thu", "Thu")], [4, this._tr("ap_fri", "Fri")], [5, this._tr("ap_sat", "Sat")], [6, this._tr("ap_sun", "Sun")],
    ];
    const eventDefs = [
      ["prewarn", this._tr("ap_ev_prewarn", "Heads-up before the run (with \"Skip tonight\" on mobile)")],
      ["start", this._tr("ap_ev_start", "Run starts")],
      ["backup_ok", this._tr("ap_ev_backup_ok", "Backup succeeded")],
      ["backup_failed", this._tr("ap_ev_backup_failed", "Backup failed — Autopilot disengaged (always on)")],
      ["update_failed", this._tr("ap_ev_update_failed", "An update failed / run blocked")],
      ["summary", this._tr("ap_ev_summary", "Summary when the run is done")],
      ["restarted", this._tr("ap_ev_restarted", "Home Assistant is back after a restart")],
      ["nothing_to_do", this._tr("ap_ev_nothing", "No updates to install (confirms Autopilot ran)")],
      ["new_components", this._tr("ap_ev_new", "New components not in the schedule")],
    ];
    const skipNext = !!ap.state?.skip_next;
    const newCount = (ap.new_unscheduled || []).length;

    return `
      <div class="ap">
        ${codeRed ? `
          <div class="fl-banner fl-banner-critical">
            <span class="fl-banner-icon">🔴</span>
            <span class="fl-banner-text"><strong>Autopilot disengaged</strong> — ${this._tr("ap_code_red", "OPCON is Code Red. Autopilot will not run until it is reset.")}</span>
            <button class="btn fl-reset-btn" onclick="this.getRootNode().host._resetOpcon()">${this._tr("fl_reset_btn", "Reset OPCON")}</button>
          </div>` : ""}
        ${newCount ? `
          <div class="ap-banner-new">🆕 ${newCount} ${this._tr("ap_new_banner", "new component(s) are installed but not in the schedule (\"Only selected\"). Tick them below if Autopilot should update them, then save.")}</div>` : ""}

        <div class="ap-card ap-hero">
          <div class="ap-hero-main">
            <label class="ap-switch">
              <input type="checkbox" ${d.enabled ? "checked" : ""} onchange="this.getRootNode().host._apSet('enabled', this.checked)">
              <span class="ap-switch-label">${d.enabled ? "✈️ " + this._tr("ap_engaged", "Autopilot engaged") : this._tr("ap_off", "Autopilot off")}</span>
            </label>
            <div class="ap-hero-meta">
              <span>${this._tr("ap_next_run", "Next run")}: <strong>${ap.next_run ? this._apFormatDateTime(ap.next_run) : "—"}</strong>${skipNext ? ` <span class="ap-tag ap-tag-off">${this._tr("ap_skipped_tag", "will be skipped")}</span>` : ""}</span>
              <span>${this._tr("ap_last_status", "Last run")}: <strong class="ap-status-${status}">${this._apStatusLabel(status)}</strong></span>
              ${ap.running ? `<span class="ap-running">⏳ ${this._tr("ap_running_now", "Running now…")}</span>` : ""}
            </div>
          </div>
          <div class="ap-hero-actions">
            ${ap.next_run ? `<button class="btn btn-skip" onclick="this.getRootNode().host._apSkipNext(${skipNext ? "false" : "true"})">${skipNext ? this._tr("ap_unskip", "Don't skip") : this._tr("ap_skip_next", "Skip next run")}</button>` : ""}
            <button class="btn btn-update" ${codeRed || ap.running || this._apDirty ? "disabled" : ""} title="${this._apDirty ? this._tr("ap_save_first", "Save your changes first") : ""}" onclick="this.getRootNode().host._apRunNow()">${this._tr("ap_run_now", "Run now")}</button>
          </div>
        </div>

        <div class="ap-grid">
          <div class="ap-col">
          <div class="ap-card">
            <h3>${this._tr("ap_what", "What to install")}</h3>
            <div class="ap-type">
              <div class="ap-type-head">
                <span class="type-chip" style="background:${this._typeColor("core")}">${this._typeLabel("core")}</span>
                <div class="ap-seg">
                  <button class="ap-seg-btn${!d.core ? " active" : ""}" onclick="this.getRootNode().host._apSet('core', false)">${this._tr("ap_mode_off", "Off")}</button>
                  <button class="ap-seg-btn${d.core ? " active" : ""}" onclick="this.getRootNode().host._apSet('core', true)">${this._tr("ap_mode_on", "On")}</button>
                </div>
              </div>
              <div class="ap-hint">${this._tr("ap_core_hint", "Core always runs last — it restarts Home Assistant by itself.")}</div>
            </div>
            ${this._renderApTypeBlock("addon", this._typeLabel("addon"))}
            ${this._renderApTypeBlock("hacs", this._typeLabel("hacs"))}
            <div class="ap-note">ⓘ ${this._tr("ap_unsupported", "Autopilot only handles Core, apps (add-ons) and HACS. Supervisor, OS, device firmware and other updates are not supported and are never touched.")}</div>
          </div>
          <div class="ap-card">
            <h3>${this._tr("ap_preview", "If Autopilot ran now")} ${this._apDirty ? `<span class="ap-muted ap-small">(${this._tr("ap_preview_unsaved", "with your unsaved changes")})</span>` : ""}</h3>
            <div class="ap-sub">✅ ${this._tr("ap_ready", "Would be installed")} (${ev.ready.length})</div>
            ${this._renderApPreviewList(ev.ready, false)}
            <div class="ap-sub">⏳ ${this._tr("ap_waiting", "Waiting for minimum age")} (${ev.waiting.length})</div>
            ${this._renderApPreviewList(ev.waiting, true)}
            <div class="ap-sub">🚫 ${this._tr("ap_excluded", "Not included")} (${ev.excluded.length})</div>
            ${this._renderApPreviewList(ev.excluded, true)}
          </div>
          </div>

          <div class="ap-col">
            <div class="ap-card">
              <h3>${this._tr("ap_when", "When")}</h3>
              <div class="ap-row">
                <label>${this._tr("ap_time", "Time")}</label>
                ${(() => {
                  const [hh, mm] = String(d.time || "03:00").split(":");
                  const btn = (part, delta, sym, title) =>
                    `<button class="ap-step" title="${title}" onclick="this.getRootNode().host._apStepTime('${part}', ${delta})">${sym}</button>`;
                  const hourTitle = this._tr("ap_hours", "Hours");
                  const minTitle = this._tr("ap_minutes", "Minutes");
                  return `
                    <div class="ap-clock">
                      <div class="ap-steps">${btn("h", 1, "▲", hourTitle)}${btn("h", -1, "▼", hourTitle)}</div>
                      <div class="ap-clock-face"><span>${this._escHtml(hh)}</span>:<span>${this._escHtml(mm)}</span></div>
                      <div class="ap-steps">${btn("m", 5, "▲", minTitle)}${btn("m", -5, "▼", minTitle)}</div>
                    </div>`;
                })()}
              </div>
              <div class="ap-days">
                ${days.map(([n, l]) => `<button class="ap-day${(d.weekdays || []).includes(n) ? " active" : ""}" onclick="this.getRootNode().host._apToggleWeekday(${n})">${l}</button>`).join("")}
              </div>
              ${(d.weekdays || []).length === 0 ? `<div class="ap-warn">${this._tr("ap_no_days", "No weekdays selected — Autopilot will never run.")}</div>` : ""}
            </div>

            <div class="ap-card">
              <h3>${this._tr("ap_rules", "Rules")}</h3>
              <div class="ap-row">
                <label>${this._tr("ap_min_age", "Wait at least (days) after release")}</label>
                <input type="text" inputmode="numeric" maxlength="3" data-focus="min_age_days" class="ap-input ap-num" value="${this._escHtml(this._apRaw.min_age_days ?? d.min_age_days)}" oninput="this.getRootNode().host._apNumInput('min_age_days', this.value, 0, 360)" onchange="this.getRootNode().host._apNumCommit('min_age_days', 0, 360)">
              </div>
              <div class="ap-hint">${this._tr("ap_min_age_hint", "Lets the rest of the world find the bugs in .0 releases first. Default 7.")}</div>
              <label class="ap-check">
                <input type="checkbox" ${d.include_major ? "checked" : ""} onchange="this.getRootNode().host._apSet('include_major', this.checked)">
                ${this._tr("ap_include_major", "Include major version updates")}
              </label>
              <div class="ap-hint ${d.include_major ? "ap-warn" : ""}">${this._tr("ap_major_hint", "Not recommended: major versions often contain breaking changes. Leave this off and install them by hand.")}</div>
            </div>

            <div class="ap-card">
              <h3>${this._tr("ap_notify", "Notifications")}</h3>
              <div class="ap-sub">${this._tr("ap_notify_where", "Send to")}</div>
              ${this._renderApTargets()}
              <div class="ap-sub">${this._tr("ap_notify_when", "Notify me when")}</div>
              ${eventDefs.map(([k, l]) => `
                <label class="ap-check${k === "backup_failed" ? " ap-locked" : ""}"><input type="checkbox" ${k === "backup_failed" || d.notify_events?.[k] ? "checked" : ""} ${k === "backup_failed" ? "disabled" : ""} onchange="this.getRootNode().host._apToggleEvent('${k}')"> ${l}</label>
                ${k === "prewarn" && d.notify_events?.prewarn ? `
                  <div class="ap-row ap-indent">
                    <label>${this._tr("ap_prewarn_hours", "Hours before the run")}</label>
                    <input type="text" inputmode="numeric" maxlength="2" data-focus="prewarn_hours" class="ap-input ap-num" value="${this._escHtml(this._apRaw.prewarn_hours ?? d.prewarn_hours)}" oninput="this.getRootNode().host._apNumInput('prewarn_hours', this.value, 1, 24)" onchange="this.getRootNode().host._apNumCommit('prewarn_hours', 1, 24)">
                  </div>` : ""}`).join("")}
              ${this._renderApAdvanced()}
            </div>

          <div class="ap-card">
            <h3>${this._tr("ap_last_run", "Last run")}</h3>
            ${this._renderApLastRun()}
            <div class="ap-kv"><span>${this._tr("ap_streak", "Successful runs in a row")}</span><strong>${ap.state?.success_streak || 0}</strong></div>
            ${ap.state?.last_backup ? `<div class="ap-kv"><span>${this._tr("ap_last_backup", "Last Autopilot backup")}</span><span>${this._apFormatDateTime(ap.state.last_backup.at)}</span></div>` : ""}
          </div>
          </div>
        </div>


        ${this._apDirty ? `
          <div class="ap-savebar">
            <span>${this._tr("ap_unsaved", "You have unsaved changes")}</span>
            <button class="btn btn-skip" onclick="this.getRootNode().host._apDiscard()">${this._tr("ap_discard", "Discard")}</button>
            <button class="btn btn-update" ${this._apSaving ? "disabled" : ""} onclick="this.getRootNode().host._apSave()">${this._apSaving ? this._tr("ap_saving", "Saving…") : this._tr("ap_save", "Save")}</button>
          </div>` : ""}
      </div>`;
  }

  // Development aid: only shown for "-dev" builds, so releases look unchanged.
  // Also flags a stale browser cache (JS file older than the running backend).
  _renderDevBadge() {
    const backend = this._config?.version || "";
    const jsVersion = new URLSearchParams(_SCRIPT_VERSION_QUERY).get("v") || "";
    if (!backend.includes("-dev") && !jsVersion.includes("-dev")) return "";
    const stale = backend && jsVersion && backend !== jsVersion;
    return stale
      ? ` <span class="dev-badge dev-badge-stale" title="Browser: ${this._escHtml(jsVersion)} · Home Assistant: ${this._escHtml(backend)}">DEV ${this._escHtml(backend)} · ${this._tr("dev_reload", "panel is outdated — press Ctrl+F5")}</span>`
      : ` <span class="dev-badge">DEV ${this._escHtml(backend || jsVersion)}</span>`;
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
    const active = this.shadowRoot.activeElement;
    const focusKey = active?.dataset?.focus || null;
    const caret = focusKey ? active.selectionStart : null;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 16px; font-family: var(--paper-font-body1_-_font-family, sans-serif); }
        .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
        .header h1 { margin: 0; font-size: 1.5rem; font-weight: 500; color: var(--primary-text-color); }
        .dev-badge { display: inline-block; margin-left: 10px; vertical-align: middle; padding: 2px 9px; border-radius: 10px; background: #6a1b9a; color: white; font-size: 0.75rem; font-weight: 600; letter-spacing: 0.3px; font-family: monospace; }
        .dev-badge-stale { background: #c62828; }
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
        .fl-toggle-btn { background: none; border: 1px solid var(--divider-color, #e0e0e0); color: var(--secondary-text-color); border-radius: 4px; padding: 6px 14px; cursor: pointer; font-size: 0.875rem; }
        .fl-toggle-btn:hover { background: var(--secondary-background-color, #f5f5f5); }
        .fl-toggle-btn.active { border-color: var(--primary-color, #03a9f4); color: var(--primary-color, #03a9f4); background: rgba(3,169,244,0.08); font-weight: 600; }
        .fl-banner { display: flex; align-items: center; gap: 12px; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; color: white; }
        .fl-banner-running { background: #0288d1; }
        .fl-banner-critical { background: #c62828; }
        .fl-banner-icon { font-size: 1.1rem; flex-shrink: 0; }
        .fl-banner-text { flex: 1; font-size: 0.9rem; line-height: 1.4; }
        .fl-reset-btn { background: white; color: #c62828; border: none; font-weight: 600; flex-shrink: 0; }
        .fl-reset-btn:hover { background: rgba(255,255,255,0.85); }
        .fl-panel { border: 1px solid var(--divider-color, #e0e0e0); border-radius: 8px; padding: 16px; margin-bottom: 20px; background: var(--card-background-color, white); }
        .fl-info-box { margin-bottom: 14px; }
        .fl-info-toggle { background: none; border: none; color: var(--secondary-text-color); cursor: pointer; font-size: 0.85rem; padding: 0; display: flex; align-items: center; gap: 6px; }
        .fl-info-toggle:hover { color: var(--primary-text-color); }
        .fl-info-arrow { font-size: 0.7rem; }
        .fl-info-body { margin-top: 8px; padding-left: 4px; }
        .fl-order { font-weight: 600; color: var(--primary-text-color); margin: 4px 0; font-size: 0.9rem; }
        .fl-info-hint { font-size: 0.8rem; color: var(--secondary-text-color); line-height: 1.5; margin: 4px 0 0; }
        .fl-commit-row { display: flex; gap: 10px; flex-wrap: wrap; }
        .fl-btn-day { background: var(--primary-color, #03a9f4); color: white; padding: 10px 18px; font-size: 0.9rem; font-weight: 600; flex: 1; min-width: 220px; }
        .fl-btn-day:hover:not(:disabled) { filter: brightness(1.1); }
        .fl-btn-crash { background: none; border: 1px solid #c62828; color: #c62828; padding: 8px 14px; font-size: 0.8rem; }
        .fl-btn-crash:hover:not(:disabled) { background: rgba(198,40,40,0.08); }
        .fl-checkbox-cell { width: 32px; text-align: center; }
        .fl-not-eligible { color: var(--secondary-text-color); }
        .fl-th { width: 32px; }
        .fl-confirm-dialog { max-width: 480px; }
        .fl-confirm-steps { max-height: 240px; overflow-y: auto; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 6px; padding: 8px 12px; margin-bottom: 16px; }
        .fl-confirm-group { margin-bottom: 8px; }
        .fl-confirm-group:last-child { margin-bottom: 0; }
        .fl-confirm-phase { font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.4px; color: var(--secondary-text-color); }
        .fl-confirm-steps ul { margin: 2px 0 0; padding-left: 20px; }
        .fl-danger-ack { display: flex; align-items: flex-start; gap: 8px; font-size: 0.85rem; color: var(--secondary-text-color); margin-bottom: 16px; line-height: 1.4; cursor: pointer; }
        .ap-card { background: var(--card-background-color, white); border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); padding: 16px; margin-bottom: 16px; }
        .ap-card h3 { margin: 0 0 12px; font-size: 1rem; font-weight: 600; color: var(--primary-text-color); }
        .ap-grid { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 16px; align-items: start; }
        .ap-grid > .ap-card, .ap-col > .ap-card:last-child { margin-bottom: 0; }
        .ap-grid { margin-bottom: 16px; }
        .ap-col { display: flex; flex-direction: column; gap: 16px; }
        .ap-col > .ap-card { margin-bottom: 0; }
        .ap-hero { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
        .ap-hero-main { display: flex; flex-direction: column; gap: 8px; }
        .ap-hero-meta { display: flex; gap: 18px; flex-wrap: wrap; font-size: 0.85rem; color: var(--secondary-text-color); }
        .ap-hero-actions { display: flex; gap: 8px; flex-wrap: wrap; }
        .ap-hero-actions .btn { padding: 8px 16px; font-size: 0.875rem; }
        .ap-switch { display: flex; align-items: center; gap: 10px; cursor: pointer; font-size: 1.15rem; font-weight: 600; color: var(--primary-text-color); }
        .ap-switch input { width: 20px; height: 20px; cursor: pointer; }
        .ap-running { color: #0288d1; font-weight: 600; }
        .ap-type { padding: 12px 0; border-top: 1px solid var(--divider-color, #e0e0e0); }
        .ap-type:first-of-type { border-top: none; padding-top: 0; }
        .ap-type-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
        .ap-seg { display: inline-flex; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 6px; overflow: hidden; flex-wrap: wrap; }
        .ap-seg-btn { background: none; border: none; border-left: 1px solid var(--divider-color, #e0e0e0); padding: 6px 12px; cursor: pointer; font-size: 0.8rem; color: var(--secondary-text-color); }
        .ap-seg-btn:first-child { border-left: none; }
        .ap-seg-btn.active { background: var(--primary-color, #03a9f4); color: white; font-weight: 600; }
        .ap-hint { font-size: 0.8rem; color: var(--secondary-text-color); margin: 6px 0 0; line-height: 1.45; }
        .ap-warn { color: var(--warning-color, #ff9800); font-weight: 500; }
        .ap-note { margin-top: 12px; padding: 10px 12px; background: var(--secondary-background-color, #f5f5f5); border-radius: 6px; font-size: 0.8rem; color: var(--secondary-text-color); line-height: 1.45; }
        .ap-comp-box { margin-top: 10px; }
        .ap-comp-list { max-height: 260px; overflow-y: auto; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 6px; padding: 4px 0; }
        .ap-comp { display: flex; align-items: center; gap: 8px; padding: 6px 12px; cursor: pointer; font-size: 0.875rem; color: var(--primary-text-color); flex-wrap: wrap; }
        .ap-comp:hover { background: var(--secondary-background-color, #f5f5f5); }
        .ap-comp-title { flex: 1; min-width: 120px; }
        .ap-tag { font-size: 0.7rem; padding: 1px 7px; border-radius: 10px; background: var(--secondary-background-color, #eee); color: var(--secondary-text-color); white-space: nowrap; }
        .ap-tag-on { background: rgba(76,175,80,0.15); color: #2e7d32; }
        .ap-tag-off { background: rgba(198,40,40,0.12); color: #c62828; }
        .ap-tag-pending { background: rgba(3,169,244,0.12); color: #0277bd; }
        .ap-tag-new { background: rgba(255,152,0,0.18); color: #e65100; }
        .ap-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 8px 0; font-size: 0.875rem; color: var(--primary-text-color); }
        .ap-indent { padding-left: 26px; margin-top: 0; }
        .ap-input { padding: 6px 8px; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 4px; background: var(--card-background-color, white); color: var(--primary-text-color); font-size: 0.875rem; }
        .ap-num { width: 72px; }
        .ap-chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
        .ap-chip { display: inline-flex; align-items: center; gap: 4px; padding: 4px 4px 4px 10px; border-radius: 16px; background: rgba(3,169,244,0.12); color: var(--primary-text-color); font-size: 0.85rem; border: 1px solid transparent; }
        .ap-chip-ok { background: rgba(76,175,80,0.15); border-color: rgba(76,175,80,0.5); }
        .ap-chip-fail, .ap-chip-missing { background: rgba(198,40,40,0.12); border-color: rgba(198,40,40,0.5); }
        .ap-chip-x { background: none; border: none; cursor: pointer; color: var(--secondary-text-color); font-size: 0.75rem; padding: 2px 6px; border-radius: 50%; }
        .ap-chip-x:hover { background: rgba(0,0,0,0.08); color: var(--primary-text-color); }
        .ap-add { background: none; border: 1px dashed var(--primary-color, #03a9f4); color: var(--primary-color, #03a9f4); border-radius: 16px; padding: 4px 12px; cursor: pointer; font-size: 0.85rem; }
        .ap-add:hover { background: rgba(3,169,244,0.08); }
        .ap-menu { margin-top: 8px; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 8px; padding: 8px; background: var(--card-background-color, white); box-shadow: 0 4px 16px rgba(0,0,0,0.12); }
        .ap-menu-search { max-width: none; width: 100%; box-sizing: border-box; }
        .ap-menu-list { max-height: 240px; overflow-y: auto; margin-top: 6px; }
        .ap-menu-group { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; color: var(--secondary-text-color); padding: 8px 6px 4px; }
        .ap-menu-item { display: flex; justify-content: space-between; align-items: center; gap: 10px; width: 100%; background: none; border: none; text-align: left; padding: 7px 10px; border-radius: 6px; cursor: pointer; font-size: 0.875rem; color: var(--primary-text-color); }
        .ap-menu-item:hover { background: var(--secondary-background-color, #f5f5f5); }
        .ap-menu-id { font-size: 0.72rem; color: var(--secondary-text-color); font-family: monospace; }
        .ap-menu-foot { display: flex; justify-content: flex-end; margin-top: 6px; }
        .ap-test-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 10px; font-size: 0.85rem; }
        .ap-test-row .btn { padding: 7px 14px; font-size: 0.85rem; }
        .ap-ok { color: #2e7d32; }
        .ap-adv { margin-top: 14px; margin-bottom: 0; }
        .ap-yaml { background: var(--secondary-background-color, #f5f5f5); border-radius: 6px; padding: 10px 12px; font-size: 0.75rem; overflow-x: auto; color: var(--primary-text-color); user-select: all; }
        .ap-clock { display: inline-flex; align-items: center; gap: 8px; }
        .ap-steps { display: flex; flex-direction: column; gap: 3px; }
        .ap-step { width: 34px; height: 24px; padding: 0; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 4px; background: var(--card-background-color, white); color: var(--primary-color, #03a9f4); cursor: pointer; font-size: 0.7rem; line-height: 1; user-select: none; }
        .ap-step:hover { background: var(--secondary-background-color, #f5f5f5); }
        .ap-step:active { background: var(--primary-color, #03a9f4); color: white; }
        .ap-clock-face { font-size: 1.6rem; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--primary-text-color); padding: 4px 10px; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 6px; min-width: 96px; text-align: center; letter-spacing: 1px; }
        .ap-days { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
        .ap-day { background: none; border: 1px solid var(--divider-color, #e0e0e0); border-radius: 16px; padding: 5px 12px; cursor: pointer; font-size: 0.8rem; color: var(--secondary-text-color); }
        .ap-day.active { background: var(--primary-color, #03a9f4); border-color: var(--primary-color, #03a9f4); color: white; font-weight: 600; }
        .ap-check { display: flex; align-items: flex-start; gap: 8px; font-size: 0.875rem; color: var(--primary-text-color); margin: 6px 0; cursor: pointer; line-height: 1.35; }
        .ap-check input { margin-top: 2px; }
        .ap-locked { opacity: 0.8; cursor: default; }
        .ap-sub { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; color: var(--secondary-text-color); margin: 14px 0 6px; }
        .ap-sub:first-of-type { margin-top: 0; }
        .ap-list { list-style: none; margin: 0; padding: 0; }
        .ap-list li { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; padding: 6px 0; border-bottom: 1px solid var(--divider-color, #e0e0e0); font-size: 0.85rem; }
        .ap-list li:last-child { border-bottom: none; }
        .ap-li-title { font-weight: 500; color: var(--primary-text-color); margin-right: 6px; }
        .ap-reason { margin-left: auto; font-size: 0.75rem; color: var(--secondary-text-color); }
        .ap-muted { color: var(--secondary-text-color); font-size: 0.85rem; padding: 4px 0; }
        .ap-small { font-size: 0.75rem; font-weight: 400; }
        .ap-kv { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; font-size: 0.85rem; color: var(--primary-text-color); border-bottom: 1px solid var(--divider-color, #e0e0e0); }
        .ap-kv > span:first-child { color: var(--secondary-text-color); white-space: nowrap; }
        .ap-kv > span:last-child { text-align: right; }
        .ap-err { color: var(--error-color, #db4437); }
        .ap-status-success { color: #2e7d32; }
        .ap-status-failed, .ap-status-backup_failed, .ap-status-blocked { color: #c62828; }
        .ap-status-running { color: #0288d1; }
        .ap-banner-new { background: rgba(255,152,0,0.15); border: 1px solid rgba(255,152,0,0.5); color: var(--primary-text-color); border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 0.875rem; line-height: 1.45; }
        .ap-savebar { position: sticky; bottom: 12px; display: flex; align-items: center; justify-content: flex-end; gap: 10px; background: var(--card-background-color, white); border: 1px solid var(--primary-color, #03a9f4); border-radius: 8px; padding: 10px 14px; box-shadow: 0 4px 16px rgba(0,0,0,0.18); z-index: 5; }
        .ap-savebar span { flex: 1; font-size: 0.875rem; color: var(--primary-text-color); }
        .ap-savebar .btn { padding: 8px 18px; font-size: 0.875rem; }
        @media (max-width: 900px) {
          .ap-grid { grid-template-columns: minmax(0, 1fr); }
        }
        @media (max-width: 640px) {
          .ap-hero-actions { width: 100%; }
          .ap-hero-actions .btn { flex: 1; }
          .ap-reason { margin-left: 0; width: 100%; }
          .fl-commit-row { flex-direction: column; }
          .fl-btn-day, .fl-btn-crash { min-width: 0; }
        }
      </style>
      <div class="header">
        <h1>${this._tr("panel_title", "Update Manager")}${this._renderDevBadge()}</h1>
        <div class="header-actions">
          ${isPending ? this._renderSortButtons() : ""}
          <button class="ha-update-btn" onclick="this.getRootNode().host._navigateToHaUpdates()">${this._tr("ha_updates_btn", "HA Updates ↗")}</button>
          ${isPending ? `<button class="toggle-skipped-btn${this._showSkipped ? " active" : ""}" onclick="this.getRootNode().host._toggleSkipped()">${this._showSkipped ? this._tr("hide_skipped_btn", "Hide skipped") : this._tr("show_skipped_btn", "Show skipped")}</button>` : ""}
          ${isPending && this._config.feeling_lucky_enabled ? `<button class="fl-toggle-btn${this._flActive ? " active" : ""}" onclick="this.getRootNode().host._toggleFeelingLucky()">${this._flActive ? this._tr("fl_hide", "Hide Feeling Lucky") : this._tr("fl_show", "🍀 Feeling Lucky")}</button>` : ""}
          <button class="refresh-btn" onclick="this.getRootNode().host._refresh()">${this._tr("refresh_btn", "Refresh list")}</button>
        </div>
      </div>
      <div class="tab-bar">
        <button class="tab-btn${this._view === "pending" ? " active" : ""}" onclick="this.getRootNode().host._setView('pending')">${this._tr("tab_pending", "Pending updates")}</button>
        <button class="tab-btn${this._view === "installed" ? " active" : ""}" onclick="this.getRootNode().host._setView('installed')">${this._tr("tab_installed", "Currently installed")}</button>
        <button class="tab-btn${this._view === "history" ? " active" : ""}" onclick="this.getRootNode().host._setView('history')">${this._tr("tab_history", "Latest installed")}</button>
        <button class="tab-btn${this._view === "autopilot" ? " active" : ""}" onclick="this.getRootNode().host._setView('autopilot')">✈️ ${this._tr("tab_autopilot", "Autopilot")}</button>
      </div>
      ${isPending ? this._renderRestartBanner() : ""}
      ${isPending ? this._renderFeelingLuckyBanner() : ""}
      ${isPending ? this._renderFeelingLuckyPanel() : ""}
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
      ${this._view === "autopilot" ? this._renderAutopilot() : ""}
      ${this._renderConfirmModal()}
      ${this._renderFlConfirmModal()}
    `;
    // Full innerHTML re-render destroys the focused field — put focus and
    // caret back on the Autopilot input the user was typing in.
    if (focusKey) {
      const el = this.shadowRoot.querySelector(`[data-focus="${focusKey}"]`);
      if (el) {
        el.focus();
        try { el.setSelectionRange(caret ?? el.value.length, caret ?? el.value.length); } catch { /* not a text field */ }
      }
    }
  }
}

customElements.define("advanced-update-manager-panel", AdvancedUpdateManagerPanel);
