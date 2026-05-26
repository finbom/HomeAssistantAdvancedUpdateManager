# Changelog

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
