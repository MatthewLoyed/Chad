"""Release discovery for the Windows installer channel."""

import re
import sys
from pathlib import Path

import httpx

from src.shared.frozen_bootstrap import is_frozen

RELEASES_URL = "https://github.com/MatthewLoyed/Chad/releases"
RELEASES_API = "https://api.github.com/repos/MatthewLoyed/Chad/releases"
ALPHA_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)-alpha\.(\d+)")


def packaged_version() -> str | None:
    """Identify NSIS installs separately from binaries built in a checkout."""
    if not is_frozen():
        return None
    try:
        return Path(sys.executable).with_name("chad_version.txt").read_text(
            encoding="ascii"
        ).strip() or None
    except (OSError, UnicodeError):
        return None


def release_status(installed: str) -> dict:
    status = {"installed": installed, "releases_url": RELEASES_URL}
    current = ALPHA_VERSION.fullmatch(installed)
    if current is None:
        return status
    try:
        response = httpx.get(RELEASES_API, params={"per_page": 30}, timeout=5.0)
        response.raise_for_status()
        releases = response.json()
    except (httpx.HTTPError, ValueError):
        return status
    if not isinstance(releases, list):
        return status

    versions = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or not release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        version = ALPHA_VERSION.fullmatch(tag[1:]) if isinstance(tag, str) and tag.startswith("v") else None
        if version is None:
            continue
        installer = f"Chad-Setup-{version[0]}.exe"
        assets = release.get("assets")
        if isinstance(assets, list) and any(
            isinstance(asset, dict) and asset.get("name") == installer for asset in assets
        ):
            versions.append((tuple(map(int, version.groups())), version[0]))
    if versions:
        key, latest = max(versions)
        status["latest"] = latest
        if key > tuple(map(int, current.groups())):
            status["installer_url"] = f"{RELEASES_URL}/download/v{latest}/Chad-Setup-{latest}.exe"
    return status
