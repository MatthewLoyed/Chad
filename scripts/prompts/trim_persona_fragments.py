"""
trim_persona_fragments.py — #248 prompt consolidation (content trims only).

Applies benign, reversible CONTENT reductions to the Supabase Cloud Master
(`persona_config`), each as a new version (old versions retained → revert by
re-activating an earlier row). NO scope-token changes here: worker-scope
stripping is done in code (`prompt_builder._read_agent_files`), which is
deploy-gated and never touches the live Brain's data.

What it does:
  1. identity   — drop UI branding/visual block; fold the unique bits of `user`
                  (expertise) into the User line. Tone stays in `soul` (no dup).
  2. user       — neutralize (render-skipped) now that identity carries it.
  3. memory_policy — merge the duplicated MEMORY section out of `agent` into one
                  tightened policy (kills the biggest near-duplicate).
  4. agent      — reduce to the WORKSPACE root only, via `{project_root}`.

(`supervisor_routing` and `planner` already use the `{active_agents}` placeholder,
so the specialist roster is not duplicated in raw content — nothing to do there.)

Usage:
  $env:PYTHONPATH="."; .venv\Scripts\python.exe scripts\prompts\trim_persona_fragments.py          # dry run
  $env:PYTHONPATH="."; .venv\Scripts\python.exe scripts\prompts\trim_persona_fragments.py --write   # apply
"""
import asyncio
import os
import sys

os.environ.setdefault("CHAD_TESTING", "true")

# A single space: truthy at warmup (so the older non-empty version is NOT re-added
# from the version-desc scan), but `.strip()` is empty so the builder render loop
# skips it. This is how we retire a fragment without a hard DELETE.
NEUTRALIZED = " "

NEW_IDENTITY = """# CHAD

Name:         Chad
Relationship: Peer / collaborator — a capable friend, not a tool or servant.
User:         {{USER_NAME}} — set to your own name/profile in config. Treat as a peer; no hand-holding.
Email:        {{USER_EMAIL}}  (use to sign into things when needed)
Timezone:     {{USER_TIMEZONE}}
Signature:    🦾

## Style
Casual but sharp — talks like a person, not a product.
Dry humor, delivered once, never labored over."""

NEW_MEMORY_POLICY = """# MEMORY POLICY

Personal facts about the user live in ONE store: the knowledge graph
(`config/personal_knowledge.md` → `data/knowledge_graph.json`, GraphRAGClient).
`manage_user_memory` (`save`/`search`/`forget`/`status`) is the ONLY write path —
never persist personal identity or preferences anywhere else.

Read path: query-matched facts are injected under `### PERSONAL CONTEXT` (and sometimes
`### RELATIONAL CONTEXT BOOST`). Use those when present; `search` to verify a personal
fact before asserting it when that context is empty or ambiguous.

## When to save
- Tier 1 (sensitive: health, relationships, finances, legal, credentials, employer/school):
  ask first — "Want me to remember that?" — save only on explicit yes.
- Tier 2 (durable prefs, schedules, project context, people): offer once, save on yes.
- Tier 3 (low-risk operational mappings: device names, paths, nicknames): save, then confirm briefly.

## When to update / forget
- Injected context contradicts the user → `forget` the stale fact, save the correction (Tier 1: ask).
- "forget that" / "that's wrong" → `forget` immediately.
- Time-sensitive facts (deadlines, travel) → trust what the user says now; injected context may be stale.

## Don't save
One-off task instructions, transient mood/state, or uncertain paraphrases (search first; if unsure, ask)."""

NEW_AGENT = """# WORKSPACE

Active project root: `{project_root}`
All filesystem operations and commands target this folder unless told otherwise."""


async def main(write: bool) -> None:
    from src.shared.config_manager import ConfigManager
    from src.infrastructure.repositories.prompt_registry import PromptRegistry
    from src.infrastructure.repositories.memory_repository import MemoryRepository
    ConfigManager.load_config()

    reg = PromptRegistry.get_instance()
    await reg.warmup()
    repo = MemoryRepository()

    updates = [
        ("identity", NEW_IDENTITY, None),
        ("user", NEUTRALIZED, None),
        ("memory_policy", NEW_MEMORY_POLICY, None),
        ("agent", NEW_AGENT, None),
    ]

    from src.core.utils.token_counter import TokenCounter
    print(f"{'mode':6} {'id':20} {'old tok':>8} {'new tok':>8}")
    for pid, new_content, scope in updates:
        old = reg._prompts.get(pid, "")
        ot, nt = TokenCounter.count_text(old), TokenCounter.count_text(new_content)
        changed = old.strip() != new_content.strip()
        mode = ("WRITE" if write else "dry") if changed else "skip"
        print(f"{mode:6} {pid:20} {ot:>8} {nt:>8}")
        if write and changed:
            ok = repo.update_persona_config(pid, new_content, scope=scope)
            print(f"       -> {'ok' if ok else 'FAILED'}")

    if not write:
        print("\nDry run. Re-run with --write to apply to Supabase (versioned/reversible).")


if __name__ == "__main__":
    asyncio.run(main("--write" in sys.argv))
