"""GitHub API client — fetches release dates for update entities."""
from __future__ import annotations

import base64
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
    """Return the add-on subfolder name from a monorepo URL.
    
    Handles multiple URL patterns:
    - /tree/branch/subfolder (e.g., https://github.com/home-assistant/addons/tree/master/samba)
    - /blob/branch/subfolder/file (e.g., https://github.com/home-assistant/addons/blob/master/samba/README.md)
    - Just the first path segment after branch (for URLs with trailing paths)
    
    Returns the first-level subfolder name (e.g., 'samba', 'mosquitto', etc.)
    """
    # Pattern 1: /tree/branch/{subfolder}[/...]
    match = re.search(r"github\.com/[^/]+/[^/?#]+/tree/[^/]+/([^/?#]+)", url)
    if match:
        return match.group(1)
    
    # Pattern 2: /blob/branch/{subfolder}[/...]
    match = re.search(r"github\.com/[^/]+/[^/?#]+/blob/[^/]+/([^/?#]+)", url)
    if match:
        return match.group(1)
    
    return None


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
    """Return addon metadata dict from the Supervisor REST API, or None.

    If the add-on's own url field doesn't point to GitHub (e.g. it's the
    project website), we follow up with a store/repositories lookup to get
    the actual Git source URL of the add-on repository.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with session.get(
            f"http://supervisor/addons/{slug}/info",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = (await resp.json()).get("data") or {}

        # If the add-on url is not a GitHub URL, try the store repository source.
        if "github.com" not in (data.get("url") or ""):
            repo_id = data.get("repository", "")
            if repo_id and repo_id not in ("core", "local"):
                try:
                    async with session.get(
                        f"http://supervisor/store/repositories/{repo_id}",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as repo_resp:
                        if repo_resp.status == 200:
                            repo_data = (await repo_resp.json()).get("data") or {}
                            source = repo_data.get("source", "")
                            if source and "github.com" in source:
                                data["url"] = source
                except Exception as exc:
                    _LOGGER.debug("Supervisor repo lookup failed for %s: %s", repo_id, exc)

        return data
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


async def fetch_ha_addon_registry_date(
    session: aiohttp.ClientSession,
    addon_slug: str,
    version: str,
) -> str | None:
    """Fetch release date from Home Assistant's official add-on registry monorepo."""
    return await fetch_release_date(
        session, "home-assistant", "addons", version, token=None, tag_prefix=addon_slug
    )


async def fetch_addon_config_date(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    version: str,
    token: str | None = None,
    subpath: str | None = None,
    slug: str | None = None,
) -> str | None:
    """Find the commit date when an add-on config was bumped to the given version.

    Strategy (in order):
    1. Check commits to config.yaml / config.json at the known subpath(s).
       The commit whose file content contains the target version is the release.
    2. Search recent commit messages on the default branch for the version string.
       Most maintainers include the version in the commit message.

    Returns YYYY-MM-DD or None.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    bare = version.lstrip("v")
    version_re = re.compile(
        r"""["\']?version["\']?\s*[:=]\s*["\']?""" + re.escape(bare) + r"""["\']?""",
        re.IGNORECASE,
    )

    # Build ordered, deduplicated list of config paths to check.
    # Try subpath and slug variants before falling back to repo root.
    paths: list[str] = []
    seen: set[str] = set()
    for prefix in filter(None, [subpath, slug if slug != subpath else None]):
        for fname in ("config.yaml", "config.json"):
            p = f"{prefix}/{fname}"
            if p not in seen:
                paths.append(p)
                seen.add(p)
    for fname in ("config.yaml", "config.json"):
        if fname not in seen:
            paths.append(fname)
            seen.add(fname)

    for path in paths:
        try:
            async with session.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/commits",
                headers=headers,
                params={"path": path, "per_page": 5},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    continue
                commits = await resp.json()
        except Exception as exc:
            _LOGGER.debug("commit list failed %s/%s %s: %s", owner, repo, path, exc)
            continue

        for commit in commits:
            sha = commit.get("sha", "")
            commit_info = commit.get("commit") or {}
            raw_date = (
                (commit_info.get("committer") or {}).get("date")
                or (commit_info.get("author") or {}).get("date")
                or ""
            )
            try:
                async with session.get(
                    f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
                    headers=headers,
                    params={"ref": sha},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
            except Exception as exc:
                _LOGGER.debug("content fetch failed %s@%s: %s", path, sha, exc)
                continue

            if version_re.search(content):
                _LOGGER.debug("config commit date %s/%s@%s via %s: %s", owner, repo, version, path, raw_date[:10])
                return raw_date[:10] if raw_date else None

    # Fallback: scan recent commit messages for the version string.
    # Most maintainers include the version in the commit message (e.g. "Bump to 12.6.1").
    version_msg_re = re.compile(r"\b" + re.escape(bare) + r"\b")
    try:
        async with session.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/commits",
            headers=headers,
            params={"per_page": 50},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                for commit in await resp.json():
                    commit_info = commit.get("commit") or {}
                    if version_msg_re.search(commit_info.get("message", "")):
                        raw_date = (
                            (commit_info.get("committer") or {}).get("date")
                            or (commit_info.get("author") or {}).get("date")
                            or ""
                        )
                        if raw_date:
                            _LOGGER.debug("commit message date %s/%s@%s: %s", owner, repo, version, raw_date[:10])
                            return raw_date[:10]
    except Exception as exc:
        _LOGGER.debug("commit message search failed %s/%s: %s", owner, repo, exc)

    return None
