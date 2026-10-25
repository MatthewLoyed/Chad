"""Version, artifact provenance and download-link helpers for Windows releases."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def next_version(releases: list[dict]) -> str:
    versions = []
    for release in releases:
        match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)-alpha\.(\d+)", release["tag_name"])
        if match:
            versions.append(tuple(map(int, match.groups())))
    if not versions:
        raise ValueError("No alpha release baseline; supply --version explicitly")
    major, minor, patch, alpha = max(versions)
    return f"{major}.{minor}.{patch}-alpha.{alpha + 1}"


def validate_version(version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9]+(?:\.[a-zA-Z0-9]+)*)?", version):
        raise ValueError("Expected a semantic version without a leading v")
    return version


def write_provenance(installer: Path, version: str, source: str) -> Path:
    validate_version(version)
    if installer.name != f"Chad-Setup-{version}.exe" or not installer.is_file():
        raise ValueError("Installer name must match the release version")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Source commit must be a full Git SHA")
    with installer.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = installer.with_name("release-manifest.json")
    manifest.write_text(json.dumps({
        "version": version, "source_commit": source, "installer": installer.name,
        "bytes": installer.stat().st_size, "sha256": digest,
    }, indent=2) + "\n", encoding="utf-8")
    installer.with_name("SHA256SUMS").write_text(f"{digest}  {installer.name}\n", encoding="utf-8")
    return manifest


def update_download(readme: Path, tag: str) -> None:
    version = validate_version(tag.removeprefix("v"))
    if tag != f"v{version}":
        raise ValueError("Release tags must begin with v")
    text = readme.read_text(encoding="utf-8")
    # Only the opening download block owns the current installer version.
    start = text.index("## Download Chad for Windows")
    end = text.index("\nStart with Chat mode", start)
    block, count = re.subn(r"(?:v)?\d+\.\d+\.\d+(?:-[a-zA-Z0-9]+(?:\.\d+)*)?",
                          lambda m: ("v" if m[0].startswith("v") else "") + version,
                          text[start:end])
    if count < 3:
        raise ValueError("README download block has no recognized release links")
    readme.write_text(text[:start] + block + text[end:], encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["version", "manifest", "readme"])
    parser.add_argument("--version", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--source", default="")
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args()
    if args.command == "version":
        version = args.version or args.tag.removeprefix("v")
        if not version:
            releases = json.loads(subprocess.check_output(
                ["gh", "api", "repos/{owner}/{repo}/releases?per_page=100"], text=True))
            version = next_version(releases)
        print(validate_version(version))
    elif args.command == "manifest":
        print(write_provenance(args.installer, args.version, args.source))
    else:
        update_download(args.readme, args.tag)


if __name__ == "__main__":
    main()
