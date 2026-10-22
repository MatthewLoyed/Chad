"""
update_web_search_persona.py — Align persona fragments with web-search routing.

Fixes stale prompts that still mandate delegate-only web research or reference
removed tools (deep_research, brave_web_search). Matches:
  src/infrastructure/tools/web_search_routing.py
  docs/features/WEB_SEARCH.md

Rows use environment=`dev` (the only environment Chad loads at runtime).
Edits are versioned/reversible via MemoryRepository.update_persona_config.

Usage:
    $env:PYTHONPATH="."; python scripts/update_web_search_persona.py
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from src.infrastructure.repositories.memory_repository import MemoryRepository
from scripts.web_search_persona_content import PERSONA_UPDATES


def main() -> int:
    print("Applying web-search persona alignment (versioned, reversible)...\n")
    repo = MemoryRepository()
    rc = 0
    for config_id, content, scope in PERSONA_UPDATES:
        cur = repo.get_persona_config(config_id) or ""
        if cur.strip() == content.strip():
            print(f"  [SKIP] {config_id}: already current")
            continue
        ok = repo.update_persona_config(config_id, content, scope=scope)
        print(f"  [{'OK' if ok else 'FAIL'}] {config_id}: written (scope={scope})")
        if not ok:
            rc = 1
    print("\nRestart the Brain after applying so PromptRegistry reloads these fragments.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
