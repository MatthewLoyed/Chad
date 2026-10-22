"""
persona_store.py — local-first, file-backed persona fragment store (issue #589).

Persona prompts are **files**, not database rows. Two layers resolve by fragment id:

    prompts/persona/<id>.md    baseline  — generic, git-tracked, ships with the repo
    config/persona/<id>.md     overlay   — this user's persona, gitignored, personal

The overlay wins per-id; a fragment with no overlay falls through to the baseline.
That is the whole read path: no network, no database, no warmup round-trip. A fresh
clone works because the baseline is committed; a six-month-old install works because
its overlay sits next to it.

Why files rather than rows (the prompt-management consensus, and #358's requirement):
prompts change more often than any other part of an LLM stack, so they need to be
diffable, reviewable in a PR, and revertable with the same tools as code. Rows in a
database are none of those. It also makes the Chad-on-Chad loop possible at all —
the coder edits `config/persona/planner.md` and the eval loads that exact file, so an
improvement is actually measurable. When the prompt under test and the prompt in
production are different artifacts, the loop reports "no improvement" forever.

Version history still matters (reflection lessons write unattended, and a bad
auto-write needs a way back), so every write appends to a `persona_history` table in
the local SQLite store *before* touching the file. History is an audit/rollback log
that is never consulted on the read path — files stay the source of truth.

Supabase is no longer read here. It remains available as an optional sync/backup
target for multi-device users (see scripts/setup/migrate_persona_to_local.py), but
Chad's persona no longer depends on it being reachable, configured, or in sync.

Fragment file format — a minimal frontmatter block, then the prompt body:

    ---
    scope: always
    ---
    You are Chad, a personal AI assistant...
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.infrastructure.database.local_postgrest import get_local_shim, register_table
from src.shared.paths import get_project_root

logger = logging.getLogger("PersonaStore")

# Fragment ids that are no longer part of the persona and are skipped on load.
#
# `user` (#589): personal facts moved to the knowledge graph — `config/personal_knowledge.md`
# compiled into `data/knowledge_graph.json`, injected per-turn by memory_engine. reflection.py's
# GRAPH_SCOPES already routes every `user`-scope lesson there, so nothing has written this
# fragment on purpose in a long time; the live Supabase row had been blanked to a single space
# by an unattended write and no one noticed, which is exactly the failure mode of keeping a
# store nothing reads. See docs/architecture/MEMORY_AND_CONTEXT.md.
DEPRECATED_IDS = frozenset({"user"})

_FRONTMATTER_DELIM = "---"

# Accepted values for CHAD_PERSONA_LAYER — see load_fragments().
_VALID_LAYERS = frozenset({"merged", "baseline", "overlay"})

_lock = threading.Lock()


# --- paths ------------------------------------------------------------------


def baseline_dir() -> Path:
    """Generic persona shipped with the repo. Git-tracked; safe to publish."""
    return get_project_root() / "prompts" / "persona"


PERSONA_OVERLAY_ENV = "CHAD_PERSONA_OVERLAY_DIR"


def overlay_dir() -> Path:
    """This user's persona. Gitignored; may contain personal content.

    ``CHAD_PERSONA_OVERLAY_DIR`` redirects it. This exists for eval lanes that run against
    a fictional persona: the memory-write lane swaps the knowledge graph for a fixture, but
    the persona overlay was left pointing at the real ``config/persona/``, so the model
    resolved the fixture's first-person prompts to the REAL user and saved facts under
    their name — in a lane whose own contract is "never real personal data".

    Pointing this at a directory with no overlay falls through to the git-tracked baseline,
    which is generic by construction, so no fictional identity has to be invented to
    replace one that leaked.
    """
    override = os.environ.get(PERSONA_OVERLAY_ENV)
    if override and override.strip():
        return Path(override.strip())
    return get_project_root() / "config" / "persona"


def _register_local_table() -> None:
    """Declare the version-history table on the shared shim. Idempotent."""
    register_table(
        "persona_history",
        columns={
            "row_id": "INTEGER",
            "id": "TEXT",
            "content": "TEXT",
            "scope": "TEXT",
            "version": "INTEGER",
            "changelog": "TEXT",
            "source": "TEXT",
            "created_at": "TEXT",
        },
        pk="row_id",
    )


_register_local_table()


# --- fragment file (de)serialization ----------------------------------------


def _parse(text: str) -> Tuple[Optional[str], str]:
    """Split a fragment file into (scope, body).

    Frontmatter is optional and deliberately not YAML — a fragment only ever carries
    `scope`, and hand-rolling the two-line parse keeps prompt files readable by anyone
    (and editable by a model) without a parser dependency in the boot path.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return None, text.strip()

    scope: Optional[str] = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_DELIM:
            body = "\n".join(lines[i + 1:]).strip()
            return scope, body
        key, sep, value = line.partition(":")
        if sep and key.strip() == "scope":
            scope = value.strip() or None
    # Unterminated frontmatter — treat the whole file as body rather than losing it.
    logger.warning("[ PERSONA ] Unterminated frontmatter; reading file as plain body.")
    return None, text.strip()


def _render(scope: str, content: str) -> str:
    return f"{_FRONTMATTER_DELIM}\nscope: {scope}\n{_FRONTMATTER_DELIM}\n\n{content.strip()}\n"


def _read_dir(path: Path) -> Dict[str, Tuple[Optional[str], str]]:
    """Read every `*.md` in a persona directory into {id: (scope, content)}."""
    out: Dict[str, Tuple[Optional[str], str]] = {}
    if not path.is_dir():
        return out
    for f in sorted(path.glob("*.md")):
        frag_id = f.stem
        if frag_id in DEPRECATED_IDS:
            continue
        try:
            scope, body = _parse(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.error("[ PERSONA ] Failed to read %s: %s", f, e)
            continue
        if body:
            out[frag_id] = (scope, body)
        else:
            # An empty fragment contributes nothing but would shadow the baseline.
            logger.warning("[ PERSONA ] Ignoring empty fragment %s", f)
    return out


# --- read path --------------------------------------------------------------


def load_fragments() -> Dict[str, Tuple[str, str]]:
    """Resolve the effective persona: baseline, with the overlay overriding per id.

    Returns {id: (scope, content)}. An overlay fragment that omits `scope` inherits
    the baseline's scope — so a user (or Chad Code) can rewrite a prompt's prose
    without having to restate, or accidentally change, where it gets injected.
    Getting scope wrong silently sprays a fragment into every prompt, so inheriting
    is the safe default.

    `CHAD_PERSONA_LAYER` pins which layers resolve, so a run's persona is a stated
    input rather than a property of which directory happened to exist on the box:

        merged   (default) baseline + overlay, overlay wins per id
        baseline           the committed generic persona only, overlay ignored
        overlay            the personal overlay only, no baseline fallthrough

    Without this an eval launched from a fresh worktree (no overlay) and one launched
    from the main checkout silently measure different prompts and report both as the
    same number. Anything comparing personas must set it explicitly.
    """
    layer = (os.environ.get("CHAD_PERSONA_LAYER") or "merged").strip().lower()
    if layer not in _VALID_LAYERS:
        logger.warning(
            "[ PERSONA ] Unknown CHAD_PERSONA_LAYER=%r — expected one of %s. Falling "
            "back to 'merged'.", layer, ", ".join(sorted(_VALID_LAYERS)),
        )
        layer = "merged"

    baseline = _read_dir(baseline_dir()) if layer != "overlay" else {}
    overlay = _read_dir(overlay_dir()) if layer != "baseline" else {}

    if layer != "merged":
        logger.info("[ PERSONA ] CHAD_PERSONA_LAYER=%s — resolving %s only.", layer, layer)

    merged: Dict[str, Tuple[str, str]] = {}
    for frag_id, (scope, content) in baseline.items():
        merged[frag_id] = (scope or "always", content)
    for frag_id, (scope, content) in overlay.items():
        inherited = scope or (merged.get(frag_id, (None, ""))[0]) or "always"
        merged[frag_id] = (inherited, content)
    return merged


def get_fragment(frag_id: str) -> Optional[Tuple[str, str]]:
    """Return (scope, content) for one fragment, or None if it doesn't exist."""
    return load_fragments().get(frag_id)


def list_fragment_ids() -> List[str]:
    return sorted(load_fragments().keys())


def overlay_ids() -> List[str]:
    """Ids this user has customized — i.e. present in the overlay layer."""
    return sorted(_read_dir(overlay_dir()).keys())


# --- write path -------------------------------------------------------------


def _next_version(frag_id: str) -> int:
    rows = get_local_shim().query(
        "persona_history",
        select="version",
        filters={"id": f"eq.{frag_id}"},
        order="version.desc",
        limit=1,
    )
    return (rows[0].get("version", 0) + 1) if rows else 1


def write_fragment(
    frag_id: str,
    content: str,
    scope: Optional[str] = None,
    changelog: Optional[str] = None,
    source: str = "runtime",
) -> bool:
    """Write a fragment to the overlay, recording a history row first.

    `scope` is resolved from the existing fragment when not given, mirroring the
    carry-forward rule the old `update_persona_config` had to enforce by hand: a
    write that silently reset scope to 'always' would inject the fragment into every
    prompt at once.

    Writes always land in the overlay, never the baseline — the baseline is the
    generic persona published to other people and must not absorb one user's edits.
    """
    if frag_id in DEPRECATED_IDS:
        logger.warning("[ PERSONA ] Refusing to write deprecated fragment '%s'.", frag_id)
        return False
    if not content or not content.strip():
        logger.warning("[ PERSONA ] Refusing to write empty content to '%s'.", frag_id)
        return False

    existing = get_fragment(frag_id)
    resolved_scope = scope or (existing[0] if existing else None) or "always"

    with _lock:
        version = _next_version(frag_id)
        try:
            get_local_shim().insert(
                "persona_history",
                {
                    "id": frag_id,
                    "content": content,
                    "scope": resolved_scope,
                    "version": version,
                    "changelog": changelog or f"Version {version} ({source})",
                    "source": source,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as e:  # noqa: BLE001
            # History is for rollback, not correctness — never block the write on it.
            logger.error("[ PERSONA ] History write failed for '%s': %s", frag_id, e)

        try:
            path = overlay_dir() / f"{frag_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_render(resolved_scope, content), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.error("[ PERSONA ] Failed to write fragment '%s': %s", frag_id, e)
            return False

    logger.info("[ PERSONA ] Wrote '%s' (scope=%s, v%d) to overlay.", frag_id, resolved_scope, version)
    return True


# --- history / rollback -----------------------------------------------------


def history(frag_id: str, limit: int = 50) -> List[Dict]:
    """Version history for a fragment, newest first."""
    return get_local_shim().query(
        "persona_history",
        filters={"id": f"eq.{frag_id}"},
        order="version.desc",
        limit=limit,
    )


def rollback(frag_id: str, version: int) -> bool:
    """Restore a fragment to an earlier recorded version.

    Recorded as a new version rather than rewinding the log, so the fact that a
    rollback happened stays visible.
    """
    rows = get_local_shim().query(
        "persona_history",
        filters={"id": f"eq.{frag_id}", "version": f"eq.{version}"},
        limit=1,
    )
    if not rows:
        logger.error("[ PERSONA ] No version %s recorded for '%s'.", version, frag_id)
        return False
    row = rows[0]
    return write_fragment(
        frag_id,
        row.get("content", ""),
        scope=row.get("scope"),
        changelog=f"Rolled back to version {version}",
        source="rollback",
    )
