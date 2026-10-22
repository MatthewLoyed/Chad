"""Local-only evidence for explicitly allowlisted effective persona fragments.

Only selected IDs retain bytes. Other overlay files contribute hashes so an
out-of-scope edit cannot be silently discarded with clone cleanup.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _seal(value: dict) -> str:
    return _digest(json.dumps(value, sort_keys=True).encode())


def _file(root: Path, relative: str) -> dict:
    path = root / relative
    # Reject links/junctions in every component, including dangling links.
    for part in [path, *path.parents]:
        if part == root:
            break
        try:
            # Python 3.11 has no Path.is_junction(). Windows marks junctions
            # and symlinks as reparse points; inspect without following them.
            reparse = bool(getattr(part.lstat(), "st_file_attributes", 0) & 0x400)
        except FileNotFoundError:
            reparse = False
        if part.is_symlink() or reparse:
            raise ValueError("Persona path contains a link")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Persona path escapes checkout")
    if not path.exists():
        return {"path": relative, "sha256": None, "content_base64": None}
    data = path.read_bytes()
    data.decode("utf-8")  # Unreadable fragments cannot be attributable inputs.
    return {"path": relative, "sha256": _digest(data),
            "content_base64": base64.b64encode(data).decode("ascii")}


def snapshot_persona(root: Path, ids) -> dict:
    """Snapshot disk inputs; does not attest a running Brain's cached PromptRegistry."""
    from src.infrastructure.repositories.persona_store import _parse, DEPRECATED_IDS

    root = root.resolve()
    ids = list(ids)
    if (not ids or len(ids) != len(set(ids)) or
            any(not isinstance(pid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", pid)
                or pid in DEPRECATED_IDS for pid in ids)):
        raise ValueError("Require unique explicit nondeprecated persona IDs")
    if (os.environ.get("CHAD_PERSONA_LAYER") or "merged").strip().lower() != "merged":
        raise ValueError("Persona capture currently requires merged persona layer")
    if os.environ.get("CHAD_PERSONA_OVERLAY_DIR", "").strip():
        raise ValueError("Redirected persona overlay requires separate provenance; refusing capture")

    inventory = {}
    overlay = root / "config/persona"
    # Check the directory itself even if no .md files exist.
    _file(root, "config/persona/__capture_absent__")
    for path in sorted(overlay.glob("*.md")):
        record = _file(root, path.relative_to(root).as_posix())
        inventory[record["path"]] = record["sha256"]
    fragments = {}
    for pid in sorted(ids):
        baseline = _file(root, f"prompts/persona/{pid}.md")
        custom = _file(root, f"config/persona/{pid}.md")
        def parse(record):
            raw = record["content_base64"]
            # persona_store.read_text uses universal newlines; retain original bytes
            # separately while hashing the same effective text the registry reads.
            text = base64.b64decode(raw).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n") if raw is not None else ""
            return _parse(text) if raw is not None else (None, "")
        scope, body = parse(baseline)
        custom_scope, custom_body = parse(custom)
        effective = custom if custom_body else baseline
        if not (custom_body or body):
            raise ValueError(f"No effective persona fragment for {pid}")
        fragments[pid] = {"baseline": baseline, "overlay": custom,
                          "effective_path": effective["path"],
                          "effective_scope": (custom_scope if custom_body else None) or (scope if body else None) or "always",
                          "effective_body_sha256": _digest((custom_body or body).encode())}
    snapshot = {"version": 1, "layer": "merged", "fragments": fragments,
                "overlay_inventory": inventory}
    snapshot["sha256"] = _seal(snapshot)
    return snapshot


def validate_snapshot(snapshot: dict) -> None:
    payload = {key: value for key, value in snapshot.items() if key != "sha256"}
    if _seal(payload) != snapshot.get("sha256"):
        raise ValueError("Persona snapshot hash mismatch")
    for fragment in snapshot["fragments"].values():
        for layer in ("baseline", "overlay"):
            record = fragment[layer]
            raw = record["content_base64"]
            digest = _digest(base64.b64decode(raw, validate=True)) if raw is not None else None
            if digest != record["sha256"]:
                raise ValueError("Persona file hash mismatch")


def require_matching_persona(root: Path, snapshot: dict) -> None:
    validate_snapshot(snapshot)
    if snapshot_persona(root, snapshot["fragments"]) != snapshot:
        raise ValueError("Persona inputs drifted from the baseline snapshot")


def require_effective_scope(snapshot: dict, paths) -> None:
    """A tracked-template-only scope cannot authorize a shadowed prompt repair."""
    for fragment in snapshot["fragments"].values():
        if fragment["baseline"]["path"] in paths and fragment["effective_path"] not in paths:
            raise ValueError("Repair scope omits the active persona overlay")


def capture_persona_changes(root: Path, before: dict) -> dict:
    validate_snapshot(before)
    after = snapshot_persona(root, before["fragments"])
    allowed = {fragment["overlay"]["path"] for fragment in before["fragments"].values()}
    old, new = before["overlay_inventory"], after["overlay_inventory"]
    if any(old.get(path) != new.get(path) for path in old.keys() | new.keys() if path not in allowed):
        raise ValueError("Unallowlisted persona overlay changed; retain clone for local review")
    changes = {pid: {"before": fragment, "after": after["fragments"][pid]}
               for pid, fragment in before["fragments"].items() if fragment != after["fragments"][pid]}
    result = {"baseline_sha256": before["sha256"], "after_sha256": after["sha256"],
              "changed": bool(changes), "fragments": changes}
    result["sha256"] = _seal(result)
    return result
