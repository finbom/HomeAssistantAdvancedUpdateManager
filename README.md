![Logo](https://raw.githubusercontent.com/finbom/HomeAssistantAdvancedUpdateManager/main/Assets/LogoUpdateManager.png)

# Home Assistant Advanced Update Manager

[![Ko-Fi](https://img.shields.io/badge/Ko--fi-Support%20me-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/finbom)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-Support%20me-FFDD00?logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/nkzdjs5gzk)

A HACS custom integration that adds a dedicated **Update Manager** panel to your Home Assistant sidebar — with enriched information that the built-in update UI doesn't show..

## Features

- **All update types in one view** — Core, HA OS, Add-ons, HACS integrations, Device firmware, and Other
- **Real release dates** — fetched from GitHub and cached persistently (survives restarts)
- **Direct links** to release notes for each update
- **Backup before update** — one-click backup + install
- **Skip** a specific version without losing the update notification
- **Real-time sync** — automatically reflects updates installed via the normal HA UI
- **Sensor entities** — expose update statistics to dashboards, automations, and scripts

## Installation via HACS

1. Open HACS in your Home Assistant
2. Go to **Integrations** → click the three-dot menu → **Custom repositories**
3. Add `https://github.com/finbom/HomeAssistantAdvancedUpdateManager` with category **Integration**
4. Click **Add** → search for "Advanced Update Manager" and install it
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration** and search for "Advanced Update Manager"
7. Click **Submit** — a new **Update Manager** entry appears in the sidebar

## Manual installation

1. Copy `custom_components/advanced_update_manager` into your HA config's `custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration** and search for "Advanced Update Manager"

## How it works

- The integration registers itself as a **custom panel** in HA's sidebar (not an add-on, no separate Docker container)
- It runs inside HA's own process and subscribes to `update.*` entity state changes
- Release dates are fetched from the GitHub Releases API and stored persistently in `.storage/advanced_update_manager.json`
- Updates installed via the normal HA UI are automatically removed from the panel's list

## Sensors

Advanced Update Manager creates a set of HA sensor entities grouped under a single **Advanced Update Manager** device in **Settings → Devices & Services**. Use them in dashboards, automations, and scripts.

> **Entity IDs are prefixed with the device name.** Because these sensors use HA's device-scoped naming, the entity ID is the device name and the sensor name slugified together — so the **Pending Updates** sensor is `sensor.advanced_update_manager_pending_updates`, *not* `sensor.pending_updates`. The full IDs are listed below. If you renamed the device or an entity, your IDs will differ — check **Developer Tools → States** and filter on `advanced_update_manager` to see the exact names in your install.

| Sensor | Example value | Description |
|--------|--------------|-------------|
| **Pending Updates**<br>`sensor.advanced_update_manager_pending_updates` | `7` | Total pending updates. Attributes include per-type counts: `core`, `haos`, `addon`, `hacs`, `device`, `other`. Ideal for sidebar badges or notification automations. |
| **Oldest Pending Update**<br>`sensor.advanced_update_manager_oldest_pending_update` | `47 days` | Age in days of the oldest available update (based on release date). Attributes identify the specific update. |
| **All Pending Updates**<br>`sensor.advanced_update_manager_all_pending_updates` | `7` | Full list of all pending updates exposed as the `pending_updates` attribute, sorted oldest-first. Each item includes `entity_id`, `title`, `type`, `installed_version`, `latest_version`, `release_date`, `days_pending`, `major_version_change`, `release_url`, and `skipped`. Use in notification automations to list every pending update, or filter in templates by type, age, or whether the version was skipped. |
| **Days Since Last Install**<br>`sensor.advanced_update_manager_days_since_last_install` | `12 days` | The "accident counter" — days since anything was installed. Resets to `0` on every install. Attributes show what was last installed. |
| **Last Installed**<br>`sensor.advanced_update_manager_last_installed` | `ESPHome 2025.5.1` | Name and version of the most recently installed update. Attributes include entity ID, type, from/to version, and install date. |
| **Updates Released This Week**<br>`sensor.advanced_update_manager_updates_released_this_week` | `4` | Number of pending updates released in the last 7 days. Spikes after major HA releases. |
| **Major Version Updates Pending**<br>`sensor.advanced_update_manager_major_version_updates_pending` | `1` | Count of updates where the major version number changed — a proxy for breaking changes. Attributes list the specific updates. |
| **History Log Size**<br>`sensor.advanced_update_manager_history_log_size` | `48 KB` | Size of the persistent storage file on disk. Use in automations to alert when the log grows unexpectedly large. |
| **Total Installs**<br>`sensor.advanced_update_manager_total_installs` | `312` | Lifetime count of all tracked installs in the retained history. |
| **Avg Days Release to Install**<br>`sensor.advanced_update_manager_avg_days_release_to_install` | `4.2 days` | Average time between a release being published and you installing it — your personal update hygiene score. |
| **Longest Gap Without Update**<br>`sensor.advanced_update_manager_longest_gap_without_update` | `34 days` | All-time record of the longest gap between consecutive installs — your personal best on the "accident counter". |
| **Most Updated Integration**<br>`sensor.advanced_update_manager_most_updated_integration` | `ESPHome` | The integration you have updated most often. Attributes include the install count and entity ID. |

## Automation & Dashboard Examples

The `sensor.advanced_update_manager_all_pending_updates` sensor exposes all pending updates as a list in the `pending_updates` attribute. Below are ready-to-use examples.

### Daily digest notification

Sends a morning summary of everything waiting to be installed. Skipped versions are excluded.

```yaml
alias: Daily pending update digest
trigger:
  - platform: time
    at: "09:00:00"
condition:
  - condition: numeric_state
    entity_id: sensor.advanced_update_manager_all_pending_updates
    above: 0
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "{{ states('sensor.advanced_update_manager_all_pending_updates') }} pending updates"
      message: >
        {% set updates = state_attr('sensor.advanced_update_manager_all_pending_updates', 'pending_updates') %}
        {% for u in updates if not u.skipped %}
        • {{ u.title }} ({{ u.type }}) — {{ u.days_pending }} days old
        {% endfor %}
```

---

### Immediate alert for major version changes

Fires as soon as a breaking update appears — useful for Core or add-ons where a major bump may require manual action.

```yaml
alias: Alert on major version update
trigger:
  - platform: state
    entity_id: sensor.advanced_update_manager_all_pending_updates
action:
  - condition: template
    value_template: >
      {{ state_attr('sensor.advanced_update_manager_all_pending_updates', 'pending_updates')
         | selectattr('major_version_change', 'eq', true) | list | count > 0 }}
  - service: notify.mobile_app_your_phone
    data:
      title: "Breaking update available"
      message: >
        {% set updates = state_attr('sensor.advanced_update_manager_all_pending_updates', 'pending_updates') %}
        {% for u in updates if u.major_version_change %}
        ⚠️ {{ u.title }}: {{ u.installed_version }} → {{ u.latest_version }}
        {% if u.release_url %}{{ u.release_url }}{% endif %}
        {% endfor %}
```

---

### Nag when updates have been waiting too long

Reminds you every morning about updates that have been available for more than 14 days and haven't been skipped.

```yaml
alias: Nag for stale updates
trigger:
  - platform: time
    at: "09:00:00"
action:
  - condition: template
    value_template: >
      {{ state_attr('sensor.advanced_update_manager_all_pending_updates', 'pending_updates')
         | selectattr('skipped', 'eq', false)
         | selectattr('days_pending', 'defined')
         | selectattr('days_pending', 'gt', 14) | list | count > 0 }}
  - service: notify.mobile_app_your_phone
    data:
      title: "Updates waiting over 14 days"
      message: >
        {% set updates = state_attr('sensor.advanced_update_manager_all_pending_updates', 'pending_updates') %}
        {% for u in updates if not u.skipped and u.days_pending and u.days_pending > 14 %}
        • {{ u.title }} — {{ u.days_pending }} days
        {% endfor %}
```

---

### Dashboard markdown card

Shows all pending updates (excluding skipped) as a formatted table in any Lovelace dashboard.

```yaml
type: markdown
content: >
  ## Pending Updates
  {% set updates = state_attr('sensor.advanced_update_manager_all_pending_updates', 'pending_updates') %}
  {% set visible = updates | selectattr('skipped', 'eq', false) | list %}
  {% if visible %}
  | Name | Type | Waiting |
  |------|------|---------|
  {% for u in visible %}
  | {{ u.title }} | {{ u.type }} | {{ u.days_pending if u.days_pending != none else '?' }} days |
  {% endfor %}
  {% else %}
  ✅ No pending updates.
  {% endif %}
```

---

## Update types

| Badge | Meaning |
|-------|---------|
| Core | Home Assistant Core |
| HA OS | Home Assistant Operating System |
| Add-on | Supervisor add-ons |
| HACS | HACS integrations, cards and themes |
| Device | Device firmware (ESPHome, Z-Wave, etc.) |
| Other | Anything else |

## Language / Translations

The panel automatically uses the same language as your Home Assistant UI (set under **Profile → Language**).

If no translation file exists for your language, the panel falls back to **English**.

### Currently supported languages

| Language | File |
|----------|------|
| English | `en.json` (default / fallback) |
| Swedish | `sv.json` |

### Contributing a translation

1. Fork this repository
2. Copy `custom_components/advanced_update_manager/frontend/translations/en.json` to a new file named with your language code (e.g. `de.json` for German, `nl.json` for Dutch — use the [BCP 47](https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry) language tag that Home Assistant uses)
3. Translate all values in the new file — **do not change the keys**
4. Open a pull request

That's it. No changes to any Python or JavaScript files are needed.

## Screenshots

Sort by release date.
![Sort by date](assets/AU-sortbydate.jpg)

Sort by update type. (Core, HACS, Apps etc...)
![Sort by type](assets/AU-sortbytype.jpg)

Another screenshot.
![Sort by type](assets/AU1.jpg)

View for what is currently installed.
![Sort by type](assets/AU2.jpg)

View for what has been installed.
![Sort by type](assets/AU3.jpg)


## License

MIT
