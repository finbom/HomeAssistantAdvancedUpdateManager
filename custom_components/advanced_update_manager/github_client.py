"""GitHub API client — fetches release dates for update entities."""
from __future__ import annotations

import logging
import os
import re

import aiohttp
import xml.etree.ElementTree as ET

_LOGGER = logging.getLogger(__name__)
GITHUB_API = "https://api.github.com"


def extract_owner_repo(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL."""
    match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if match:
        return match.group(1), match.group(2).rstrip("/")
    return None


def extract_monorepo_subpath(url: str) -> str | None:
    """Return the add-on subfolder name from a monorepo URL like .../tree/main/samba."""
    match = re.search(r"github\.com/[^/]+/[^/?#]+/(?:tree|blob)/[^/]+/([^/?#]+)", url)
    return match.group(1) if match else None


async def fetch_release_date_from_atom(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    version: str,
) -> str | None:
    """Fetch release date from the GitHub Atom feed — no auth, no REST rate limit.

    Only covers the latest ~10 releases, so this is a fast, cheap step before
    falling back to the git-tag API.
    """
    bare = {version, f"v{version}"} if not version.startswith("v") else {version, version[1:]}
    url = f"https://github.com/{owner}/{repo}/releases.atom"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(text)
        for entry in root.findall("a:entry", ns):
            # Prefer the <link> href which always encodes the tag name cleanly
            link_el = entry.find("a:link", ns)
            id_el = entry.find("a:id", ns)
            tag = ""
            if link_el is not None:
                href = link_el.get("href", "")
                if "/releases/tag/" in href:
                    tag = href.rsplit("/releases/tag/", 1)[-1]
            if not tag and id_el is not None:
                tag = (id_el.text or "").rsplit("/", 1)[-1]
            if tag in bare:
                updated_el = entry.find("a:updated", ns)
                if updated_el is not None and updated_el.text:
                    return updated_el.text[:10]
    except Exception as exc:
        _LOGGER.debug("Atom feed failed for %s/%s: %s", owner, repo, exc)
    return None


async def _fetch_tag_date(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    tag: str,
    headers: dict,
) -> str | None:
    """Return the date a git tag was created, resolving annotated tags to commit date."""
    try:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/tags/{tag}"
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            obj = (await resp.json()).get("object", {})
            sha = obj.get("sha")
            obj_type = obj.get("type")
            if not sha:
                return None

        if obj_type == "tag":
            # Annotated tag: resolve to get tagger date or the underlying commit
            tag_url = f"{GITHUB_API}/repos/{owner}/{repo}/git/tags/{sha}"
            async with session.get(tag_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tagger_date = (data.get("tagger") or {}).get("date", "")
                    if tagger_date:
                        return tagger_date[:10]
                    sha = (data.get("object") or {}).get("sha", sha)

        commit_url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{sha}"
        async with session.get(commit_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                date = ((data.get("commit") or {}).get("committer") or {}).get("date", "")
                return date[:10] if date else None
    except Exception as exc:
        _LOGGER.debug("Tag date lookup failed %s/%s@%s: %s", owner, repo, tag, exc)
    return None


async def fetch_release_date(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    version: str,
    token: str | None = None,
    tag_prefix: str | None = None,
) -> str | None:
    """Return published_at (YYYY-MM-DD) for the given version tag, or None.

    tag_prefix: prepend '{prefix}-' candidates for monorepo add-ons
    (e.g. prefix='samba' tries 'samba-12.6.1' before bare '12.6.1').
    Falls back to git tag commit dates when no formal GitHub Release exists.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    bare = [version, f"v{version}"] if not version.startswith("v") else [version, version[1:]]
    prefixed = [f"{tag_prefix}-{v}" for v in bare] if tag_prefix else []
    candidates = prefixed + bare

    # 1. Try GitHub Releases API (fastest, no extra calls)
    for tag in candidates:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{tag}"
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    published = data.get("published_at", "")
                    if published:
                        return published[:10]
                if resp.status == 403:
                    _LOGGER.warning("GitHub API rate limit hit for %s/%s", owner, repo)
                    return None
        except Exception as exc:
            _LOGGER.debug("GitHub release request failed for %s/%s@%s: %s", owner, repo, tag, exc)

    # 2. Atom feed — no auth, covers the latest ~10 releases without REST rate limits
    atom_date = await fetch_release_date_from_atom(session, owner, repo, version)
    if atom_date:
        return atom_date

    # 3. Fall back to git tag → commit date (handles repos without formal releases)
    for tag in candidates:
        date = await _fetch_tag_date(session, owner, repo, tag, headers)
        if date:
            _LOGGER.debug("Found tag date for %s/%s@%s: %s", owner, repo, tag, date)
            return date

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
