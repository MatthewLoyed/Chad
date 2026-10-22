"""
persona-parity — deterministic, free check that the shipped persona is complete (#589).

Chad resolves prompts from two local layers: a git-tracked generic baseline in
`prompts/persona/` and a gitignored per-user overlay in `config/persona/`. The
overlay is *supposed* to diverge — it holds a real user's evolved prompts, and on
the maintainer's own machine it holds personal content that must never be
committed. So parity here is deliberately NOT byte equality.

What actually breaks people is the opposite failure: a fragment that exists only
in someone's overlay. Their Chad works, every fresh clone silently loses that
prompt, and nobody notices because the developer's own install still has it. This
lane fails on that, plus the structural mistakes that make a fragment quietly
inert (missing scope, unknown scope, empty body, a file for a deprecated id).

Run:
    python evals/runner.py --persona-parity
"""

from __future__ import annotations

from typing import List, Tuple

from src.infrastructure.repositories import persona_store

# Scopes consumed by SystemPromptBuilder or direct PromptRegistry lookups.
KNOWN_SCOPES = {
    "always",
    "supervisor",
    "conversational",
    "heartbeat",
    "planner",
    "validator",
    "scout",
    "voice",
    "telegram",
    "contact",
    "subagent_base",
    "browser_agent",
    "canvas_agent",
    "code_agent",
    "research_agent",
    "deep_research_agent",
    "system_agent",
    "traversal_agent",
    "ui_automation_agent",
    # Loaded by id only (e.g. scout_brief_synth) — never injected via SystemPromptBuilder.build()
    "internal",
    # heartbeat_service, cron_scheduler and gmail_handler load these by id.
    "event",
}


def run_persona_parity() -> Tuple[bool, str]:
    """Returns (passed, markdown_report)."""
    baseline = persona_store._read_dir(persona_store.baseline_dir())
    overlay = persona_store._read_dir(persona_store.overlay_dir())
    effective = persona_store.load_fragments()

    failures: List[str] = []
    notes: List[str] = []

    # 1. Every overlay fragment must have a baseline counterpart, or a fresh clone
    #    boots without that prompt entirely.
    orphaned = sorted(set(overlay) - set(baseline))
    for frag_id in orphaned:
        failures.append(
            f"`{frag_id}` exists only in the overlay — a fresh clone would have no "
            f"version of this prompt. Add a generic `prompts/persona/{frag_id}.md`."
        )

    # 2. Structural validity of the effective persona.
    for frag_id, (scope, content) in sorted(effective.items()):
        if not content.strip():
            failures.append(f"`{frag_id}` resolves to empty content.")
        if scope not in KNOWN_SCOPES:
            failures.append(
                f"`{frag_id}` has unknown scope `{scope}`; check its prompt consumer."
            )

    # 3. Deprecated ids must not be reintroduced by either layer.
    for layer_name, layer_dir in (("baseline", persona_store.baseline_dir()),
                                  ("overlay", persona_store.overlay_dir())):
        for frag_id in sorted(persona_store.DEPRECATED_IDS):
            if (layer_dir / f"{frag_id}.md").exists():
                failures.append(
                    f"`{frag_id}` is deprecated but a file exists in the {layer_name} "
                    f"layer. It will be ignored at runtime — delete it."
                )

    # 4. Baseline fragments with no overlay are reported, not failed: running the
    #    generic version of a prompt is the intended default, not an error.
    uncustomized = sorted(set(baseline) - set(overlay))
    if uncustomized:
        notes.append(
            f"{len(uncustomized)} fragment(s) use the generic baseline "
            f"(no local override): {', '.join(uncustomized)}"
        )

    lines = ["# Persona parity", ""]
    lines.append(f"- baseline fragments: **{len(baseline)}** (`{persona_store.baseline_dir()}`)")
    lines.append(f"- overlay fragments: **{len(overlay)}** (`{persona_store.overlay_dir()}`)")
    lines.append(f"- effective fragments: **{len(effective)}**")
    lines.append("")

    if failures:
        lines.append(f"## ❌ {len(failures)} failure(s)")
        lines += [f"- {f}" for f in failures]
    else:
        lines.append("## ✅ Passed")
        lines.append("Every overlay fragment has a baseline counterpart; all scopes are valid.")

    if notes:
        lines += ["", "## Notes"] + [f"- {n}" for n in notes]

    return (not failures), "\n".join(lines)
