# Changelog

## [1.3.0] - 2026-06-13
### Added
- Release notes link (↗) for **HA APPS** (Supervisor add-ons) — AUM now queries the Supervisor API to resolve the add-on's GitHub repository and constructs a direct link to the release tag. Previously only Core and HACS updates had clickable release links.
- **Major version badge** on pending updates — when an update bumps the major semver component (e.g. 1.x → 2.x), an orange `⚠ Major` badge is shown next to the version number as a heads-up that breaking changes may be included.
- New `major_version_change` boolean field in the `ws_get_updates` response for automation/dashboard use.

### Changed
- Add-on Supervisor lookups are now deduplicated: the resolved GitHub URL is reused in the date-lookup path, avoiding a second API call per add-on.

## [1.2.0] - 2026-06-13
### Added
- **Not-installable indicator** for add-ons blocked by a minimum HA Core version requirement — a visual badge and tooltip show the required version instead of offering an install button.
- `installable` and `min_ha_version` fields in the `ws_get_updates` response.

## [1.1.0] - 2026-06-10
### Added
- 11 statistics sensors: Pending Updates, Oldest Pending Update, Days Since Last Install, Last Installed, Updates Released This Week, Major Version Updates Pending, History Log Size, Total Installs, Avg Days Release to Install, Longest Gap Without Update, Most Updated Integration.

## [1.0.0] - 2026-05-29
### Changed
- Version bumped to 1.0.0 — the integration has been running in production and is considered stable enough for an official 1.0 release

## [0.3.21] - 2026-05-27
### Added
- Release notes link on the Currently Installed and Latest Installed tabs — each row links directly to the GitHub release page for that version

## [0.3.20] - 2026-05-27
### Added
- Case-insensitive search/filter box on the Currently installed tab — filters by name in real time and shows match count

## [0.3.19] - 2026-05-27
### Changed
- Version bump to validate persistent install history tracking

## [0.3.18] - 2026-05-26
### Added
- Persistent install history stored in HA Storage (`/config/.storage/advanced_update_manager`) — no longer depends on recorder purge settings
- Retroactive seeding: on first startup AUM scans recorder history (up to 365 days back) to pre-populate the history register
- Configurable history retention: 30 days / 90 days / 180 days / 1 year / 2 years / Forever (default: 1 year) — set in integration options
- Storage size display in the Latest installed tab info bar
- Type badge (Core / HA OS / Apps / HACS / Device / Other) now shown in the Latest installed tab as well
- `ws_get_config` now returns `history_keep_days` and `storage_size_bytes`

### Changed
- Latest installed tab now reads from AUM's own persistent storage instead of querying recorder on every load
- Currently installed tab install dates now come from AUM storage instead of recorder

## [0.3.17] - 2026-05-26
### Added
- "Currently installed" view now shows type badge, release date, and install date columns
- Sort controls on the "Currently installed" tab: by Name, Type, Release date, Install date (ascending/descending)
- Install date is read from recorder history (most recent on→off transition per entity)
- Release date is read from the integration's persistent cache (same source as the Pending tab)

## [0.3.16] - 2026-05-26
### Added
- Three-tab navigation: **Pending updates**, **Currently installed**, **Latest installed**
- "Currently installed" view: shows all managed update entities with their current installed version (reads live HA state, no extra storage)
- "Latest installed" view: shows recent install events from HA's recorder history (on→off state transitions on `update.*` entities), with info note showing date range and a note that depth depends on recorder settings
- New WebSocket endpoints: `get_installed` and `get_history`
- Sort buttons and Show/Hide skipped toggle now only shown on the Pending tab
- Refresh button works on all tabs

## [0.3.15] - 2026-05-26
### Fixed
- Add-on date-lookup log messages downgraded from `warning` to `debug` — no longer fills the HA log on every 30-minute refresh
- Added 12 missing translation keys (`skipped_label`, `skipped_empty`, `show_skipped_btn`, `hide_skipped_btn`, `btn_release_notes`, `btn_unskip`, `confirm_skip_title`, `confirm_skip_body`, `confirm_skip_hint`, `sort_label`, `sort_type`, `sort_date`) to both `en.json` and `sv.json`

## [0.3.14] - 2026-05-26
### Fixed
- Restart banner showed constantly even when no restart was needed
- Root cause: HACS repair issues persist on disk across HA restarts — now reads `repo.pending_restart` directly from HACS runtime data instead, which resets on startup. Repair issues kept as fallback only when HACS data is unavailable.

## [0.3.13] - 2026-05-26
### Fixed
- Restart detection: only flag restart for active (non-dismissed) HACS repair issues in the `hacs` domain

## [0.3.12] - 2026-05-26
### Fixed
- Improved restart required detection — refresh state after update install

## [0.3.11] - 2026-05-26
### Fixed
- Mobile button spacing using `margin-bottom` instead of flex gap

## [0.3.10] - 2026-05-26
### Fixed
- Increased gap between mobile action buttons to 16 px

## [0.3.9] - 2026-05-26
### Fixed
- Larger mobile buttons for easier tapping

## [0.3.8] - 2026-05-24
### Fixed
- Mobile portrait spacing between update items
- JavaScript cache busting via version query parameter
- `manifest.json` key order to pass hassfest validation
- Added `http` dependency and `CONFIG_SCHEMA` for hassfest compliance

### Added
- Hassfest validation CI workflow

## [0.3.7] - 2026-05-23
### Fixed
- Mobile portrait spacing between update items

## [0.3.6] - 2026-05-23
### Fixed
- HA Updates button navigation using `CustomEvent` for the HA router (`/config/updates`)

## [0.3.5] - 2026-05-23
### Fixed
- HA Updates button navigating to wrong path

## [0.3.4] - 2026-05-23
### Fixed
- Action buttons clipped on mobile portrait layout

## [0.3.3] - 2026-05-23
### Fixed
- Backup buttons hidden on mobile — replaced backup type selector with two direct action buttons (Full backup / Addon backup)

## [0.3.2] - 2026-05-23
### Fixed
- Responsive mobile layout — update rows stack as cards on narrow screens

## [0.3.1] - 2026-05-22
### Added
- HA Core release date fetched from PyPI
- Restart-pending banner with one-click restart button

## [0.3.0] - 2026-05-22
### Added
- Internationalisation (i18n) with per-language JSON translation files
- English (`en.json`) and Swedish (`sv.json`) included

## [0.2.0] - 2026-05-21
### Added
- Update confirmation dialog
- Version display and release date fixes
- Sidebar toggle option
- HA Updates navigation button
- Allow integration reload without full HA restart

## [0.1.3] - 2026-05-20
### Fixed
- Removed `trust_external_script`, added icon to component directory

## [0.1.2] - 2026-05-20
### Fixed
- Use `async_register_static_paths` for correct static file serving

## [0.1.1] - 2026-05-20
### Fixed
- Version bump and minor corrections

## [0.1.0] - 2026-05-19
### Added
- Initial release — sidebar panel showing all pending updates with type, version, release date and install/backup/skip actions
- Multi-layer release date lookup: persistent cache → HACS store → PyPI → GitHub REST API → Atom feed → git tags → add-on config.yaml
- Config flow — no `configuration.yaml` needed
- HACS compatible
