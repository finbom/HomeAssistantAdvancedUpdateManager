"""Sensor platform for Advanced Update Manager."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    UPDATE_TYPE_ADDON,
    UPDATE_TYPE_CORE,
    UPDATE_TYPE_DEVICE,
    UPDATE_TYPE_HACS,
    UPDATE_TYPE_HAOS,
    UPDATE_TYPE_OTHER,
)
from .coordinator import UpdateManagerCoordinator

_LOGGER = logging.getLogger(__name__)

_ALL_TYPES = [
    UPDATE_TYPE_CORE,
    UPDATE_TYPE_HAOS,
    UPDATE_TYPE_ADDON,
    UPDATE_TYPE_HACS,
    UPDATE_TYPE_DEVICE,
    UPDATE_TYPE_OTHER,
]


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _major(version: str) -> int | None:
    try:
        return int(str(version).lstrip("v").split(".")[0])
    except (ValueError, IndexError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: UpdateManagerCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities([
        AUMPendingTotalSensor(coordinator, entry.entry_id),
        AUMOldestPendingSensor(coordinator, entry.entry_id),
        AUMDaysSinceLastInstallSensor(coordinator, entry.entry_id),
        AUMLastInstalledSensor(coordinator, entry.entry_id),
        AUMReleasedThisWeekSensor(coordinator, entry.entry_id),
        AUMMajorVersionPendingSensor(coordinator, entry.entry_id),
        AUMHistoryLogSizeSensor(coordinator, entry.entry_id),
        AUMTotalInstallsSensor(coordinator, entry.entry_id),
        AUMAvgDaysReleaseToInstallSensor(coordinator, entry.entry_id),
        AUMLongestStreakSensor(coordinator, entry.entry_id),
        AUMMostUpdatedSensor(coordinator, entry.entry_id),
    ])


class _AUMSensor(CoordinatorEntity[UpdateManagerCoordinator], SensorEntity):
    """Base class for all AUM sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{key}"
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Advanced Update Manager",
            manufacturer="Magnus Finbom",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _pending(self) -> list[dict]:
        return self.coordinator.data or []

    @property
    def _history(self) -> list[dict]:
        """Install history, most-recent first."""
        return self.coordinator.storage.get_install_history()


# ── 1 + 2: Pending total with per-type breakdown ─────────────────────────────

class AUMPendingTotalSensor(_AUMSensor):
    _attr_name = "Pending Updates"
    _attr_icon = "mdi:update"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "pending_updates")

    @property
    def native_value(self) -> int:
        return len(self._pending)

    @property
    def extra_state_attributes(self) -> dict:
        counts: dict[str, int] = {t: 0 for t in _ALL_TYPES}
        for u in self._pending:
            t = u.get("type", UPDATE_TYPE_OTHER)
            counts[t if t in counts else UPDATE_TYPE_OTHER] += 1
        return counts


# ── 3: Oldest pending update ──────────────────────────────────────────────────

class AUMOldestPendingSensor(_AUMSensor):
    _attr_name = "Oldest Pending Update"
    _attr_icon = "mdi:clock-alert-outline"
    _attr_native_unit_of_measurement = "days"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "oldest_pending_update")

    def _oldest(self) -> tuple[int, dict] | None:
        today = date.today()
        best: tuple[int, dict] | None = None
        for u in self._pending:
            rd = _parse_date(u.get("release_date", ""))
            if rd:
                age = (today - rd).days
                if best is None or age > best[0]:
                    best = (age, u)
        return best

    @property
    def native_value(self) -> int | None:
        result = self._oldest()
        return result[0] if result else None

    @property
    def extra_state_attributes(self) -> dict:
        result = self._oldest()
        if not result:
            return {}
        _, u = result
        return {
            "entity_id": u["entity_id"],
            "title": u["title"],
            "release_date": u.get("release_date", ""),
            "type": u.get("type", ""),
        }


# ── 4: Days since last install ────────────────────────────────────────────────

class AUMDaysSinceLastInstallSensor(_AUMSensor):
    _attr_name = "Days Since Last Install"
    _attr_icon = "mdi:history"
    _attr_native_unit_of_measurement = "days"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "days_since_last_install")

    def _last(self) -> dict | None:
        for item in self._history:  # most-recent first
            if _parse_date(item.get("install_date", "")):
                return item
        return None

    @property
    def native_value(self) -> int | None:
        last = self._last()
        if not last:
            return None
        rd = _parse_date(last["install_date"])
        return (date.today() - rd).days if rd else None

    @property
    def extra_state_attributes(self) -> dict:
        last = self._last()
        if not last:
            return {}
        return {
            "last_installed_title": last.get("title", ""),
            "last_installed_entity": last.get("entity_id", ""),
            "last_installed_version": last.get("to_version", ""),
            "last_installed_at": last.get("install_date", ""),
        }


# ── 5: Last installed ─────────────────────────────────────────────────────────

class AUMLastInstalledSensor(_AUMSensor):
    _attr_name = "Last Installed"
    _attr_icon = "mdi:package-check"

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "last_installed")

    def _last(self) -> dict | None:
        for item in self._history:
            if item.get("title"):
                return item
        return None

    @property
    def native_value(self) -> str | None:
        last = self._last()
        if not last:
            return None
        title = last.get("title", "")
        version = last.get("to_version", "")
        return f"{title} {version}".strip() or None

    @property
    def extra_state_attributes(self) -> dict:
        last = self._last()
        if not last:
            return {}
        return {
            "entity_id": last.get("entity_id", ""),
            "type": last.get("type", ""),
            "from_version": last.get("from_version", ""),
            "to_version": last.get("to_version", ""),
            "install_date": last.get("install_date", ""),
        }


# ── 6: Released this week ─────────────────────────────────────────────────────

class AUMReleasedThisWeekSensor(_AUMSensor):
    _attr_name = "Updates Released This Week"
    _attr_icon = "mdi:calendar-week"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "released_this_week")

    @property
    def native_value(self) -> int:
        cutoff = date.today() - timedelta(days=7)
        return sum(
            1 for u in self._pending
            if (rd := _parse_date(u.get("release_date", ""))) and rd >= cutoff
        )


# ── 7: Major version updates pending ─────────────────────────────────────────

class AUMMajorVersionPendingSensor(_AUMSensor):
    _attr_name = "Major Version Updates Pending"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "major_version_updates_pending")

    def _major_updates(self) -> list[dict]:
        result = []
        for u in self._pending:
            m_old = _major(u.get("installed_version", ""))
            m_new = _major(u.get("latest_version", ""))
            if m_old is not None and m_new is not None and m_new > m_old:
                result.append(u)
        return result

    @property
    def native_value(self) -> int:
        return len(self._major_updates())

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "updates": [
                {
                    "title": u["title"],
                    "from": u.get("installed_version", ""),
                    "to": u.get("latest_version", ""),
                    "type": u.get("type", ""),
                }
                for u in self._major_updates()
            ]
        }


# ── 8: History log size ───────────────────────────────────────────────────────

class AUMHistoryLogSizeSensor(_AUMSensor):
    _attr_name = "History Log Size"
    _attr_icon = "mdi:database"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "history_log_size")

    @property
    def native_value(self) -> float:
        return round(self.coordinator.storage.get_storage_size_bytes() / 1024, 1)


# ── 9: Total installs ─────────────────────────────────────────────────────────

class AUMTotalInstallsSensor(_AUMSensor):
    _attr_name = "Total Installs"
    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "total_installs")

    @property
    def native_value(self) -> int:
        return len(self._history)


# ── 10: Avg days release → install ───────────────────────────────────────────

class AUMAvgDaysReleaseToInstallSensor(_AUMSensor):
    _attr_name = "Avg Days Release to Install"
    _attr_icon = "mdi:speedometer"
    _attr_native_unit_of_measurement = "days"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "avg_days_release_to_install")

    @property
    def native_value(self) -> float | None:
        storage = self.coordinator.storage
        deltas: list[int] = []
        for item in self._history:
            install_d = _parse_date(item.get("install_date", ""))
            release_str = storage.get(item.get("entity_id", ""), item.get("to_version", ""))
            release_d = _parse_date(release_str or "")
            if install_d and release_d and install_d >= release_d:
                deltas.append((install_d - release_d).days)
        if not deltas:
            return None
        return round(sum(deltas) / len(deltas), 1)


# ── 11: Longest gap without update ───────────────────────────────────────────

class AUMLongestStreakSensor(_AUMSensor):
    _attr_name = "Longest Gap Without Update"
    _attr_icon = "mdi:trophy"
    _attr_native_unit_of_measurement = "days"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "longest_gap_without_update")

    @property
    def native_value(self) -> int | None:
        dates = sorted(
            d for d in (
                _parse_date(e.get("install_date", "")) for e in self._history
            ) if d is not None
        )
        # Deduplicate dates (multiple installs same day count as one event)
        unique_dates = sorted(set(dates))
        if len(unique_dates) < 2:
            return None
        return max((unique_dates[i] - unique_dates[i - 1]).days for i in range(1, len(unique_dates)))


# ── 12: Most updated integration ─────────────────────────────────────────────

class AUMMostUpdatedSensor(_AUMSensor):
    _attr_name = "Most Updated Integration"
    _attr_icon = "mdi:podium-gold"

    def __init__(self, coordinator: UpdateManagerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "most_updated_integration")

    def _top(self) -> dict | None:
        counts: dict[str, dict] = {}
        for item in self._history:
            eid = item.get("entity_id", "")
            if not eid:
                continue
            if eid not in counts:
                counts[eid] = {
                    "title": item.get("title", eid),
                    "count": 0,
                    "entity_id": eid,
                    "type": item.get("type", ""),
                }
            counts[eid]["count"] += 1
        if not counts:
            return None
        return max(counts.values(), key=lambda x: x["count"])

    @property
    def native_value(self) -> str | None:
        top = self._top()
        return top["title"] if top else None

    @property
    def extra_state_attributes(self) -> dict:
        top = self._top()
        if not top:
            return {}
        return {
            "install_count": top["count"],
            "entity_id": top["entity_id"],
            "type": top["type"],
        }
