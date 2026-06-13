"""DataUpdateCoordinator — fetches and enriches all update.* entities."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CORE_ENTITY_ID,
    DOMAIN,
    HAOS_ENTITY_ID,
    UPDATE_TYPE_ADDON,
    UPDATE_TYPE_CORE,
    UPDATE_TYPE_DEVICE,
    UPDATE_TYPE_HACS,
    UPDATE_TYPE_HAOS,
    UPDATE_TYPE_OTHER,
)

from .github_client import extract_monorepo_subpath, extract_owner_repo, fetch_addon_config_date, fetch_ha_addon_registry_date, fetch_pypi_release_date, fetch_release_date, fetch_supervisor_addon_info
from .storage import UpdateDateStorage

# GitHub release URL templates for entities that don't expose a GitHub release_url
_GITHUB_URL_TEMPLATES = {
    CORE_ENTITY_ID: "https://github.com/home-assistant/core/releases/tag/{version}",
    HAOS_ENTITY_ID: "https://github.com/home-assistant/operating-system/releases/tag/{version}",
}

_LOGGER = logging.getLogger(__name__)


def _addon_slug(unique_id: str) -> str:
    """Extract the add-on subfolder name from a Supervisor unique_id.

    HA appends _version_latest to the slug when creating the unique_id.
    Supervisor slugs:
    - Official HA add-ons:  core_{slug}_version_latest  → samba
    - Community add-ons:    {8-hex}_{slug}_version_latest → esphome
    """
    slug = unique_id.removesuffix("_version_latest")
    if len(slug) > 9 and slug[8] == "_" and all(
        c in "0123456789abcdefABCDEF" for c in slug[:8]
    ):
        slug = slug[9:]
    return slug.removeprefix("core_")


def _is_major_version_change(installed: str, latest: str) -> bool:
    """Return True when the major semver component increased (proxy for breaking change)."""
    try:
        i = int(str(installed).lstrip("v").split(".")[0])
        n = int(str(latest).lstrip("v").split(".")[0])
        return n > i
    except (ValueError, IndexError, AttributeError):
        return False


TYPE_ORDER = {
    UPDATE_TYPE_CORE: 0,
    UPDATE_TYPE_HAOS: 1,
    UPDATE_TYPE_ADDON: 2,
    UPDATE_TYPE_HACS: 3,
    UPDATE_TYPE_DEVICE: 4,
    UPDATE_TYPE_OTHER: 5,
}


class UpdateManagerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, storage: UpdateDateStorage) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=30),
        )
        self.storage = storage
        self.github_token: str | None = None

    async def _async_update_data(self) -> list[dict]:
        session = async_get_clientsession(self.hass)
        registry = er.async_get(self.hass)
        updates: list[dict] = []

        for state in self.hass.states.async_all("update"):
            if state.state != "on":
                continue

            entity_id = state.entity_id
            attrs = state.attributes
            new_version = attrs.get("latest_version") or ""
            current_version = attrs.get("installed_version") or ""
            release_url = attrs.get("release_url") or ""
            if not release_url or "github.com" not in release_url:
                template = _GITHUB_URL_TEMPLATES.get(entity_id)
                if template and new_version:
                    release_url = template.format(version=new_version)
            entry = registry.async_get(entity_id)
            title = (
                attrs.get("title")
                or (entry and (entry.name or entry.original_name))
                or self._format_entity_id(entity_id)
            )

            update_type = self._classify(state, entry)

            # Determine whether this update can actually be installed.
            # UpdateEntityFeature.INSTALL = 1; absence means the Supervisor has blocked it
            # (e.g. the add-on requires a newer HA Core version than what is running).
            supported_features = attrs.get("supported_features")
            installable = supported_features is None or bool(supported_features & 1)

            # For blocked add-ons, fetch the Supervisor info once here so we can surface the
            # minimum HA version requirement — and reuse it below in the release-date section
            # to avoid a second network call.
            cached_addon_info: dict | None = None
            min_ha_version = ""
            if not installable and update_type == UPDATE_TYPE_ADDON and entry and entry.unique_id:
                supervisor_slug_early = entry.unique_id.removesuffix("_version_latest")
                cached_addon_info = await fetch_supervisor_addon_info(session, supervisor_slug_early)
                min_ha_version = (cached_addon_info or {}).get("homeassistant") or ""

            # HACS entities sometimes have no release_url (HACS returns None when
            # releases aren't cached yet). Fall back to unique_id which HACS sets to
            # the repository full_name, e.g. "piitaya/lovelace-mushroom".
            release_url_constructed = False
            if update_type == UPDATE_TYPE_HACS and (not release_url or "github.com" not in release_url):
                uid = entry.unique_id if entry else None
                if uid and "/" in uid and not uid.startswith("http"):
                    base = f"https://github.com/{uid}"
                    release_url = f"{base}/releases/tag/{new_version}" if new_version else base
                    release_url_constructed = bool(new_version)

            # ADDON: always resolve GitHub URL from Supervisor so we can populate release_url
            # even when release_date is already cached (avoiding a link-less update card).
            # Reuse cached_addon_info if it was already fetched for the installable check.
            addon_github_url = ""
            if update_type == UPDATE_TYPE_ADDON and entry and entry.unique_id:
                supervisor_slug = entry.unique_id.removesuffix("_version_latest")
                addon_info = cached_addon_info if cached_addon_info is not None else await fetch_supervisor_addon_info(session, supervisor_slug)
                sv_url = (addon_info or {}).get("url", "")
                if sv_url and "github.com" in sv_url:
                    addon_github_url = sv_url
                if not addon_github_url and supervisor_slug.startswith("core_"):
                    addon_github_url = "https://github.com/home-assistant/addons"

                # Build a release_url from the supervisor GitHub URL when the update
                # entity itself doesn't expose one. Only fill an empty release_url so
                # we never discard a URL that the entity or HACS already set.
                if addon_github_url and not release_url:
                    info = extract_owner_repo(addon_github_url)
                    if info:
                        owner, repo = info
                        subpath = extract_monorepo_subpath(addon_github_url)
                        if new_version:
                            if subpath:
                                # Monorepo (home-assistant/addons): tags are "{slug}-{version}"
                                release_url = f"https://github.com/{owner}/{repo}/releases/tag/{subpath}-{new_version}"
                            else:
                                release_url = f"https://github.com/{owner}/{repo}/releases/tag/{new_version}"
                            release_url_constructed = True
                        else:
                            release_url = addon_github_url

            # Layer 1 — persistent cache
            release_date = self.storage.get(entity_id, new_version)

            # Layer 2 — HACS in-memory store (zero network calls)
            if not release_date and new_version and update_type == UPDATE_TYPE_HACS:
                uid = entry.unique_id if entry else None
                if uid and "/" in uid and not uid.startswith("http"):
                    local_date, local_url = self._get_from_hacs_store(uid, new_version)
                    if local_date:
                        release_date = local_date
                    if local_url:
                        release_url = local_url
                        release_url_constructed = False

            # Layer 3+ — remote lookups (PyPI → GitHub REST API → Atom feed → git tag)
            if not release_date and new_version:
                if entity_id == CORE_ENTITY_ID:
                    release_date = await fetch_pypi_release_date(
                        session, "homeassistant", new_version
                    )
                else:
                    # For addons reuse the already-resolved addon_github_url to avoid a
                    # duplicate Supervisor API call. For others start from release_url.
                    github_url = release_url if "github.com" in (release_url or "") else ""
                    if update_type == UPDATE_TYPE_ADDON and (not github_url) and addon_github_url:
                        github_url = addon_github_url

                    if update_type == UPDATE_TYPE_ADDON:
                        _LOGGER.debug(
                            "AUM add-on lookup: entity=%s release_url=%r addon_github_url=%r github_url=%r",
                            entity_id, release_url, addon_github_url, github_url,
                        )

                    if "github.com" in github_url:
                        info = extract_owner_repo(github_url)
                        if info:
                            owner, repo = info
                            # Use addon_github_url for subpath extraction: the constructed
                            # release_url uses a releases/tag/ path that doesn't contain the
                            # subfolder segment that extract_monorepo_subpath needs.
                            subpath_src = addon_github_url if (update_type == UPDATE_TYPE_ADDON and addon_github_url) else github_url
                            tag_prefix = extract_monorepo_subpath(subpath_src) if update_type == UPDATE_TYPE_ADDON else None
                            _LOGGER.debug("AUM add-on repo: %s/%s subpath=%r", owner, repo, tag_prefix)
                            release_date, confirmed_tag = await fetch_release_date(
                                session, owner, repo, new_version, self.github_token, tag_prefix
                            )
                            _LOGGER.debug("AUM fetch_release_date result: %r (tag=%r)", release_date, confirmed_tag)
                            if confirmed_tag and release_url_constructed:
                                release_url = f"https://github.com/{owner}/{repo}/releases/tag/{confirmed_tag}"
                                release_url_constructed = False

                            # Layer 4 — config.yaml commit date (works for add-ons without releases/tags)
                            if not release_date and update_type == UPDATE_TYPE_ADDON:
                                slug = _addon_slug(entry.unique_id) if entry and entry.unique_id else None
                                release_date = await fetch_addon_config_date(
                                    session, owner, repo, new_version, self.github_token,
                                    subpath=tag_prefix, slug=slug,
                                )
                                _LOGGER.debug("AUM fetch_addon_config_date result: %r (slug=%r)", release_date, slug)
                    elif update_type == UPDATE_TYPE_ADDON:
                        _LOGGER.debug(
                            "AUM: no GitHub URL for %s — date lookup skipped entirely",
                            entity_id,
                        )

                    # Layer 5 — HA official add-on registry (home-assistant/addons monorepo)
                    if not release_date and update_type == UPDATE_TYPE_ADDON and entry and entry.unique_id:
                        slug = _addon_slug(entry.unique_id)
                        release_date = await fetch_ha_addon_registry_date(
                            session, slug, new_version
                        )

            if release_date:
                await self.storage.async_set(entity_id, new_version, release_date)

            updates.append({
                "entity_id": entity_id,
                "title": title,
                "type": update_type,
                "installed_version": current_version,
                "latest_version": new_version,
                "release_date": release_date or "",
                "release_url": release_url,
                "skipped_version": attrs.get("skipped_version"),
                "in_progress": attrs.get("in_progress", False),
                "auto_update": attrs.get("auto_update", False),
                "installable": installable,
                "min_ha_version": min_ha_version,
                "major_version_change": _is_major_version_change(current_version, new_version),
            })

        return sorted(updates, key=lambda u: (TYPE_ORDER.get(u["type"], 99), u["title"].lower()))

    def _classify(self, state, entry: er.RegistryEntry | None) -> str:
        entity_id = state.entity_id

        if entity_id == CORE_ENTITY_ID:
            return UPDATE_TYPE_CORE
        if entity_id == HAOS_ENTITY_ID:
            return UPDATE_TYPE_HAOS

        if entry:
            if entry.platform == "hacs":
                return UPDATE_TYPE_HACS
            if entry.platform == "hassio":
                return UPDATE_TYPE_ADDON
            if entry.device_id:
                return UPDATE_TYPE_DEVICE

        return UPDATE_TYPE_OTHER

    def _get_from_hacs_store(self, full_name: str, version: str) -> tuple[str, str]:
        """Read release date and URL from HACS's in-memory repository store.

        Returns (date, url) strings; both empty when HACS isn't loaded or the
        version isn't in the cached releases list.
        """
        try:
            hacs = self.hass.data.get("hacs")
            if not hacs:
                return "", ""
            repo = hacs.repositories.get_by_full_name(full_name)
            if not repo:
                return "", ""
            bare = {version, f"v{version}"} if not version.startswith("v") else {version, version[1:]}
            for release in getattr(repo.releases, "objects", None) or []:
                tag = getattr(release, "tag_name", "") or ""
                if tag in bare:
                    url = getattr(release, "html_url", "") or ""
                    date_str = getattr(release, "published_at", "") or ""
                    return date_str[:10], url
        except Exception as exc:
            _LOGGER.debug("HACS store lookup failed for %s: %s", full_name, exc)
        return "", ""

    @staticmethod
    def _format_entity_id(entity_id: str) -> str:
        """Turn 'update.some_thing_update' into 'Some Thing'."""
        name = entity_id.removeprefix("update.")
        if name.endswith("_update"):
            name = name[:-7]
        return name.replace("_", " ").title()
