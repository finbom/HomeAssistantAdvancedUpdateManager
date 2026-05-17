"""GitHub API client — fetches release dates for update entities."""
from __future__ import annotations

import logging
import os
import re

import aiohttp

_LOGGER = logging.getLogger(__name__)
GITHUB_API = "https://api.github.com"


def extract_owner_repo(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL."""
    match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if match:
        return match.group(1), match.group(2).rstrip("/")
    return None


async def fetch_release_date(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    version: str,
    token: str | None = None,
) -> str | None:
    """Return published_at (YYYY-MM-DD) for the given version tag, or None."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    # GitHub tags may or may not have a leading 'v' — try both
    candidates = [version, f"v{version}"] if not version.startswith("v") else [version, version[1:]]

    for tag in candidates:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{tag}"
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    published = data.get("published_at", "")
                    return published[:10] if published else None
                if resp.status == 403:
                    _LOGGER.warning("GitHub API rate limit hit for %s/%s", owner, repo)
                    return None
        except Exception as exc:
            _LOGGER.debug("GitHub request failed for %s/%s@%s: %s", owner, repo, tag, exc)

    return None


async def fetch_supervisor_addon_info(
    session: aiohttp.ClientSession, slug: str
) -> dict | None:
    """Return addon metadata dict from the Supervisor REST API, or None."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        async with session.get(
            f"http://supervisor/addons/{slug}/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                return (await resp.json()).get("data") or {}
    except Exception as exc:
        _LOGGER.debug("Supervisor addon info failed for %s: %s", slug, exc)
    return None


async def fetch_pypi_release_date(
    session: aiohttp.ClientSession,
    package: str,
    version: str,
) -> str | None:
    """Return upload date (YYYY-MM-DD) from PyPI for the given package+version."""
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                for entry in data.get("urls") or []:
                    upload = entry.get("upload_time", "")
                    if upload:
                        return upload[:10]
    except Exception as exc:
        _LOGGER.debug("PyPI request failed for %s==%s: %s", package, version, exc)
    return None
