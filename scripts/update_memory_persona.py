"""
update_memory_persona.py — Phase 1 memory alignment (issue #236).

Applies reversible persona-fragment updates (local files as of #589):
  1. `memory_policy` (scope: always) — tiered remember / ask / forget rules
  2. `agent` — rewrite MEMORY section to knowledge-graph architecture

Re-running is idempotent — skips when live content already matches.

Usage:
    $env:PYTHONPATH="."; python scripts/update_memory_persona.py
"""
from __future__ import annotations

import sys
from dotenv import load_dotenv

load_dotenv()

from src.infrastructure.repositories.memory_repository import MemoryRepository

repo = MemoryRepository()

MEMORY_POLICY = """# MEMORY POLICY

Personal facts about the user live in ONE store: the knowledge graph
(`config/personal_knowledge.md` → `data/knowledge_graph.json`).
Use `manage_user_memory` for all personal fact CRUD — it is the only write path.

## When to remember

### Tier 1 — Ask before saving (sensitive / high-stakes)
Health, relationships, finances, legal, credentials, employer/school identifiers,
or anything the user might not want persisted.
Say: "Want me to remember that for next time?" Save only after explicit yes.

### Tier 2 — Offer to remember (durable preferences & life facts)
New preferences, recurring schedules, project context, people/relationships shared in chat.
When clearly new and durable, offer once: "Should I remember that?" Save on yes.

### Tier 3 — Save without asking (low-risk, session-validated)
Operational mappings validated in-session (device names, folder paths, tool nicknames).
Call `manage_user_memory` save, then briefly confirm: "Got it — I'll remember that."

## When to update or forget
- Injected `### PERSONAL CONTEXT` contradicts the user → `forget` the stale fact, then save
  the correction (or ask in Tier 1).
- User says "forget that" / "that's wrong" → `forget` immediately; save correction if given.
- Time-sensitive facts (deadlines, travel, "next Saturday") → prefer what the user says now;
  injected context may be stale.

## When NOT to save
- One-off task instructions ("run tests now")
- Transient mood/state ("I'm tired today")
- Uncertain paraphrases — search first; if unsure, ask (Tier 2) instead of saving

## Before asserting personal facts
If you are about to state something personal and `### PERSONAL CONTEXT` is empty or ambiguous,
run `manage_user_memory` search before asserting it."""

AGENT_PROTOCOL = """# AGENT PROTOCOL

## MEMORY (personal facts)
Personal facts about the user live in ONE knowledge graph:
- Human source of truth: `config/personal_knowledge.md`
- Runtime store: `data/knowledge_graph.json` (GraphRAGClient)

The system injects query-matched facts under `### PERSONAL CONTEXT` on supervisor/agent turns.
A keyword-triggered `### RELATIONAL CONTEXT BOOST` may also appear for personal topics.

**Write path**: `manage_user_memory` (`save` / `search` / `forget` / `status`) — the ONLY
tool for personal fact CRUD. Do not persist personal identity or preferences anywhere else.

**Read path**: use injected `### PERSONAL CONTEXT` when present; call `manage_user_memory`
search when you need to verify a fact before asserting it.

Follow the MEMORY POLICY component for when to save, ask, or forget.

## PROJECT WORKSPACE
Active root: `{project_root}`
All filesystem operations and commands target this folder unless told otherwise."""


def _current(config_id: str) -> str:
    return repo.get_persona_config(config_id) or ""


def upsert_memory_policy() -> bool:
    cur = _current("memory_policy")
    if cur.strip() == MEMORY_POLICY.strip():
        print("  [SKIP] memory_policy: already current")
        return True
    ok = repo.update_persona_config("memory_policy", MEMORY_POLICY, scope="always")
    print(f"  [{'OK' if ok else 'FAIL'}] memory_policy: written (scope=always)")
    return ok


def fix_agent_memory() -> bool:
    cur = _current("agent")
    if not cur:
        print("  [SKIP] agent: fragment not found")
        return False
    stale_markers = (
        "Memory is centralized in Supabase",
        "### RELEVANT RECALLED MEMORIES",
    )
    if cur.strip() == AGENT_PROTOCOL.strip():
        print("  [SKIP] agent: MEMORY section already current")
        return True
    if not any(m in cur for m in stale_markers):
        # Partial update path: replace only the MEMORY section if structure differs
        if "## MEMORY" in cur and "knowledge graph" in cur.lower():
            print("  [SKIP] agent: no stale memory-location claims detected")
            return True
    ok = repo.update_persona_config("agent", AGENT_PROTOCOL, scope="always")
    print(f"  [{'OK' if ok else 'FAIL'}] agent: MEMORY section rewritten for knowledge graph")
    return ok


def main() -> int:
    print("=== update_memory_persona (issue #236) ===")
    ok_policy = upsert_memory_policy()
    ok_agent = fix_agent_memory()
    if ok_policy and ok_agent:
        print("\nDone. Invalidate prompt cache: restart Brain or delete .cache/prompt_registry_cache.json")
        return 0
    print("\nOne or more updates failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
