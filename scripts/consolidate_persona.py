"""
consolidate_persona.py — apply the prompt-audit's consolidation fixes to Supabase.

Each fix is reversible: content edits go through update_persona_config (a NEW versioned row;
the old version is retained), and the heartbeat dedup deactivates the redundant row
(is_active=false) rather than deleting it. Re-running is idempotent — a fix is skipped if the
live content already matches the target.

Fixes (from `python -m evals.runner --prompt-audit`):
  1. supervisor_routing : '{enabled_subagents}' → '{active_agents}'  (the builder only resolves
     {active_agents}; the model was seeing a literal, un-interpolated placeholder)
  2. heartbeat          : merge heartbeat + heartbeat_protocol into ONE source of truth on the
     `heartbeat` id, then deactivate `heartbeat_protocol` (they were ~duplicates)
  3. code_agent         : drop the OUTPUT PROTOCOL block (it is byte-identical to subagent_base,
     which is auto-appended to every specialist agent)
"""
import sys
from dotenv import load_dotenv
load_dotenv()

from src.infrastructure.repositories.memory_repository import MemoryRepository

repo = MemoryRepository()


def _current(config_id: str) -> str:
    return repo.get_persona_config(config_id) or ""


MERGED_HEARTBEAT = """# Chad Proactive Alert Protocol

## Who You Are In This Moment
You're interrupting the user at their desk. Be brief, natural,
a little personality. Like a friend who just noticed something
worth mentioning — not a notification banner.

## Output Rules
- MAX 2-3 sentences for any proactive alert
- Write how you'd actually say it out loud — contractions,
  casual phrasing, natural rhythm
- Include [audio tags] for expressiveness when appropriate
  e.g. [curious], [enthusiasm], [concern]
- Do NOT use markdown, bullet points, or headers
- End with one question or action offer if relevant

## Heartbeat Checklist
1. Gmail: Any unread emails that look like they need immediate attention?
2. Calendar: Any calendar events starting within the next 90 minutes?
3. Progress: Are there any long-running tasks you should update them on?

## Decision Framework
STAY SILENT (return HEARTBEAT_OK) if:
- Email is promotional, automated, or low priority
- Calendar event is more than 2 hours away
- Nothing has materially changed since last check
- Quiet hours (23:00 - 08:00)

SPEAK UP if:
- Email from a real person that needs awareness or action
- Calendar event within 90 minutes
- Something time-sensitive or genuinely interesting

If everything is quiet, respond ONLY with "HEARTBEAT_OK".

## Delivery
- If Limb is connected (user at PC): respond naturally, TTS will speak it
- If no Limb: send to Telegram, keep it concise for mobile reading

## Example Good Output
"[curious] Hey, you just got an email from your professor —
looks like the project deadline moved up to Friday. Thought you should know."

## Example Bad Output
"**Email Alert**: You have received an email from professor@university.edu
regarding your project deadline. The deadline has been changed to Friday." """


def fix_supervisor_routing() -> bool:
    cur = _current("supervisor_routing")
    if not cur:
        print("  [SKIP] supervisor_routing: not found")
        return False
    if "{enabled_subagents}" not in cur:
        print("  [SKIP] supervisor_routing: '{enabled_subagents}' already gone")
        return False
    new = cur.replace("{enabled_subagents}", "{active_agents}")
    ok = repo.update_persona_config("supervisor_routing", new)
    print(f"  [{'OK' if ok else 'FAIL'}] supervisor_routing: {{enabled_subagents}} -> {{active_agents}}")
    return ok


def fix_heartbeat() -> bool:
    changed = False
    cur = _current("heartbeat")
    if cur.strip() != MERGED_HEARTBEAT.strip():
        ok = repo.update_persona_config("heartbeat", MERGED_HEARTBEAT)
        print(f"  [{'OK' if ok else 'FAIL'}] heartbeat: merged superset content written")
        changed = changed or ok
    else:
        print("  [SKIP] heartbeat: already merged")

    # Deactivate the now-redundant heartbeat_protocol rows.
    proto_rows = repo.supabase.query(
        repo.persona_table, select="id,is_active",
        filters={"id": "eq.heartbeat_protocol", "environment": "eq.dev", "is_active": "eq.true"})
    if proto_rows:
        ok = repo.supabase.update(
            repo.persona_table,
            {"id": "eq.heartbeat_protocol", "environment": "eq.dev"},
            {"is_active": False})
        print(f"  [{'OK' if ok else 'FAIL'}] heartbeat_protocol: deactivated {len(proto_rows)} active row(s)")
        changed = changed or ok
    else:
        print("  [SKIP] heartbeat_protocol: already deactivated")
    return changed


def fix_code_agent() -> bool:
    cur = _current("code_agent")
    if not cur:
        print("  [SKIP] code_agent: not found")
        return False
    marker = "### OUTPUT PROTOCOL:"
    if marker not in cur:
        print("  [SKIP] code_agent: OUTPUT PROTOCOL already removed")
        return False
    new = cur.split(marker)[0].rstrip()
    ok = repo.update_persona_config("code_agent", new)
    print(f"  [{'OK' if ok else 'FAIL'}] code_agent: removed duplicate OUTPUT PROTOCOL "
          f"({len(cur)} -> {len(new)} chars)")
    return ok


if __name__ == "__main__":
    print("Consolidating persona_config (versioned, reversible)...\n")
    any_changed = False
    any_changed |= fix_supervisor_routing()
    any_changed |= fix_heartbeat()
    any_changed |= fix_code_agent()
    print(f"\nDone. {'Changes applied.' if any_changed else 'No changes needed (idempotent).'}")
    print("Re-run: python -m evals.runner --prompt-audit")
