"""
update_planner_persona.py — sync planner.md doctrine to MemoryRepository (optional).

Prefer editing prompts/persona/planner.md (local-first). This script is for
environments that still push persona rows to memory.

Usage:
    $env:PYTHONPATH="."; python scripts/update_planner_persona.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from src.infrastructure.repositories.memory_repository import MemoryRepository

repo = MemoryRepository()

PLANNER_PERSONA = (
    Path("prompts/persona/planner.md").read_text(encoding="utf-8")
    # Strip YAML front matter for the stored body.
    .split("---\n", 2)[-1]
    .strip()
)


def _current() -> str:
    rows = repo.get_persona_config(environment="dev", scope="planner")
    return (rows[0].get("content") or "").strip() if rows else ""


def main() -> int:
    before = _current()
    if before == PLANNER_PERSONA:
        print("planner persona already up to date")
        return 0
    repo.upsert_persona_fragment(
        prompt_id="planner",
        scope="planner",
        content=PLANNER_PERSONA,
        environment="dev",
    )
    print("updated planner persona from prompts/persona/planner.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
