r"""
Migrate a Supabase-backed persona into the local overlay (issue #589).

For installs that predate local-first prompts: Chad's persona used to live in
Supabase's `persona_config` table, which meant the running persona existed only in
the cloud and could not be diffed, reviewed, or edited offline. This copies the
latest active version of every fragment into `config/persona/*.md`, after which
`PromptRegistry` reads those files and Supabase is no longer consulted.

Nothing is deleted. The Supabase rows stay exactly as they are, so this is
reversible by deleting the overlay files.

Fragments in `persona_store.DEPRECATED_IDS` are skipped — see that module for why
`user` is retired rather than migrated.

Usage:
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts\setup\migrate_persona_to_local.py
    # --dry-run to preview, --force to overwrite overlay files that already exist
"""

import argparse
import logging
import sys

from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.infrastructure.repositories import persona_store

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("MigratePersona")


def migrate(dry_run: bool = False, force: bool = False, environment: str = "dev") -> int:
    supabase = get_supabase_manager()
    if not supabase.enabled:
        logger.error(
            "Supabase is not configured, so there is nothing to migrate. "
            "Local persona files are already the source of truth."
        )
        return 1

    rows = supabase.query(
        "persona_config",
        filters={"is_active": "eq.true", "environment": f"eq.{environment}"},
    )
    if not rows:
        logger.error(f"No active persona_config rows found for environment '{environment}'.")
        return 1

    # Keep only the highest version per id — the table is append-only, so several
    # versions of the same fragment can be active at once.
    latest: dict = {}
    for r in rows:
        pid = r.get("id")
        if not pid:
            continue
        if pid not in latest or r.get("version", 1) > latest[pid].get("version", 1):
            latest[pid] = r

    existing_overlay = set(persona_store.overlay_ids())
    migrated, skipped, deprecated, empty = [], [], [], []

    for pid, row in sorted(latest.items()):
        content = (row.get("content") or "").strip()

        if pid in persona_store.DEPRECATED_IDS:
            deprecated.append(pid)
            continue
        if not content:
            # A blank row would otherwise shadow the baseline with nothing.
            empty.append(pid)
            continue
        if pid in existing_overlay and not force:
            skipped.append(pid)
            continue

        if dry_run:
            migrated.append(pid)
            continue

        ok = persona_store.write_fragment(
            pid,
            content,
            scope=row.get("scope"),
            changelog=f"Migrated from Supabase persona_config v{row.get('version')}",
            source="supabase_migration",
        )
        (migrated if ok else skipped).append(pid)

    verb = "Would migrate" if dry_run else "Migrated"
    logger.info(f"{verb} {len(migrated)} fragment(s) -> {persona_store.overlay_dir()}")
    for pid in migrated:
        logger.info(f"  + {pid}")
    if skipped:
        logger.info(f"Skipped {len(skipped)} already-present fragment(s) (use --force): {skipped}")
    if empty:
        logger.warning(f"Skipped {len(empty)} EMPTY Supabase fragment(s) — baseline will be used: {empty}")
    if deprecated:
        logger.info(f"Skipped {len(deprecated)} deprecated fragment(s): {deprecated}")

    if not dry_run:
        logger.info(
            "\nDone. Restart the Brain to pick these up. Supabase rows were left "
            "untouched as a backup; delete the overlay files to revert."
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files.")
    parser.add_argument("--force", action="store_true", help="Overwrite overlay files that already exist.")
    parser.add_argument("--environment", default="dev", help="persona_config environment column (default: dev).")
    args = parser.parse_args()

    try:
        sys.exit(migrate(dry_run=args.dry_run, force=args.force, environment=args.environment))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)
