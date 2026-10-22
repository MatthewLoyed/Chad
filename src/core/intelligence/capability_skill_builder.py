"""
capability_skill_builder.py — Stage 6 foundation: turn an ACCEPTED + verified capability
suggestion into a real `BaseSkill` file on disk — but ONLY behind an explicit human
approval token, never automatically.

This is the seed of Chad eventually building his own capabilities. It is deliberately the
*foundation only*: it is NOT wired to auto-run anywhere. The flow a caller must follow is:

  1. propose_skill_write(cand)            → drafts agent.py + SKILL.md, generates an approval
                                            token (reusing propose_file_plan's mechanism),
                                            and returns them for human review. NOTHING is
                                            written to disk yet.
  2. <human inspects the draft and hands the token back>
  3. commit_skill_write(token)            → validates/consumes the one-time token, and ONLY
                                            then writes the files into src/skills/<name>/ and
                                            re-scans so the skill is discoverable live.

What prevents an ungated write: `commit_skill_write` is the ONLY function that touches the
filesystem, and its very first action is `consume_approval(token)`. `consume_approval`
returns None for an unknown/expired token, in which case we return an error and write
nothing. The token is minted server-side in `propose_skill_write` and is not embedded in
the draft text, so the LLM cannot forge or self-supply it — it must come back from the human
(exactly like propose_file_plan). There is no code path that writes a skill without first
consuming a valid token.

SkillScanner re-scan post-boot: `SkillScanner.scan_directory()` instantiates and registers
any BaseSkill subclasses it finds (see skill_scanner.py) and can be called at any time with
the live registry — so a freshly-written skill becomes discoverable without a full restart.
Caveat: Python won't reload an already-imported module, so re-writing an EXISTING skill file
in the same process keeps the old class object until restart. New skill folders (the normal
case here) load fine.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("CapabilitySkillBuilder")

SKILLS_ROOT = "src/skills"
# Marks tokens minted by THIS module so we never accidentally consume a file-plan token
# (or vice-versa) — they share the same in-memory registry.
_TOKEN_PLAN_KIND = "capability_skill_write"


# ── code generation (pure, no disk, no LLM required) ─────────────────────────────


def sanitize_skill_name(title: str) -> str:
    """Coerce a candidate title into a safe snake_case python-identifier skill name.

    Pure + deterministic. Raises ValueError if nothing usable remains (so we never write
    to an attacker-influenced path like '../../etc')."""
    name = (title or "").strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    name = re.sub(r"_+", "_", name)
    if name and name[0].isdigit():
        name = f"skill_{name}"
    if not name or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"cannot derive a safe skill name from title {title!r}")
    return name


def _class_name(skill_name: str) -> str:
    return "".join(part.capitalize() for part in skill_name.split("_")) + "Skill"


def render_skill_agent(cand: Dict[str, Any], skill_name: str) -> str:
    """Render a minimal BaseSkill-conforming agent.py source for the accepted suggestion.

    The generated skill is a faithful scaffold: a specialized sub-agent persona built from
    the suggestion, no extra tools wired yet (so it is safe and inert until a human extends
    it). Pure string generation — caller decides whether/when to write it."""
    cls = _class_name(skill_name)
    title = cand.get("title", skill_name)
    description = (cand.get("description") or "").replace('"', "'").strip()
    rationale = (cand.get("rationale") or "").replace('"', "'").strip()

    # The SOP embedded as the skill's system prompt. Triple-quoted, with internal triple
    # quotes neutralized so generated code always parses.
    sop = (
        f"You are the '{title}' skill, a specialized sub-agent for Chad.\n"
        f"PURPOSE: {description}\n"
        f"WHY YOU EXIST: {rationale}\n"
        "Carry out the requested task end-to-end and return a concise, concrete result."
    ).replace('"""', "'''")

    return f'''"""
{title} — auto-scaffolded skill (Stage-6 self-building foundation).

Generated from an accepted + sandbox-verified capability suggestion. This is a SCAFFOLD:
the persona/SOP is filled in, but no extra tools are wired yet — extend `_setup_tools`
before relying on it for real work.
"""

from typing import Dict, Any, Optional

from src.core.skill_system.base_skill import BaseSkill
from src.shared.logger import setup_logger

logger = setup_logger("{cls}")


class {cls}(BaseSkill):
    name = "{skill_name}"
    description = "{description}"
    version = "0.1.0"

    def get_system_prompt(self) -> str:
        return """{sop}"""

    def _setup_tools(self) -> None:
        # No specialized tools wired yet. A human should add the tools this skill needs
        # (e.g. self.skill_registry.register_tool(...)) before production use.
        pass
'''


def render_skill_md(cand: Dict[str, Any], skill_name: str) -> str:
    """Render an accompanying SKILL.md describing the scaffolded skill. Pure."""
    return (
        f"# {cand.get('title', skill_name)}\n\n"
        f"{cand.get('description', '')}\n\n"
        f"## Why this skill exists\n{cand.get('rationale', '')}\n\n"
        f"## Status\nAuto-scaffolded from a verified capability suggestion "
        f"(confidence {float(cand.get('confidence', 0.0) or 0.0):.2f}, "
        f"backed by {int(cand.get('cluster_size', 0) or 0)} signals). "
        f"No tools wired yet — extend `_setup_tools` before relying on it.\n"
    )


# ── propose (mint token, NO disk write) ──────────────────────────────────────────


def propose_skill_write(cand: Dict[str, Any], *, skills_root: str = SKILLS_ROOT) -> Dict[str, Any]:
    """Draft the files for an accepted suggestion and mint a one-time approval token.

    Writes NOTHING to disk. Returns the rendered files + a human-readable preview + the
    token the human must hand back to `commit_skill_write`. Reuses propose_file_plan's
    token registry so the approval UX is identical.
    """
    from src.tools.filesystem.file_manager import generate_approval_token, register_approval

    try:
        skill_name = sanitize_skill_name(cand.get("title", ""))
    except ValueError as e:
        return {"success": False, "error": str(e)}

    target_dir = Path(skills_root) / skill_name
    agent_src = render_skill_agent(cand, skill_name)
    skill_md = render_skill_md(cand, skill_name)

    token = generate_approval_token()
    register_approval(
        token,
        {
            "kind": _TOKEN_PLAN_KIND,
            "skill_name": skill_name,
            "skills_root": skills_root,
            "target_dir": str(target_dir),
            "files": {"agent.py": agent_src, "SKILL.md": skill_md},
        },
        expires_minutes=15,
    )
    logger.info(f"[ CAP_BUILD ] Proposed skill write {skill_name!r} (token {token}) — awaiting human approval.")

    preview = (
        f"## Proposed new skill: `{skill_name}`\n"
        f"**{cand.get('title','')}** — {cand.get('description','')}\n\n"
        f"Would create:\n- `{target_dir / 'agent.py'}`\n- `{target_dir / 'SKILL.md'}`\n\n"
        f"To approve, hand back this token: `{token}`\n"
    )
    return {
        "success": True,
        "requires_approval": True,
        "skill_name": skill_name,
        "target_dir": str(target_dir),
        "approval_token": token,
        "preview": preview,
        "files": {"agent.py": agent_src, "SKILL.md": skill_md},
    }


# ── commit (token-gated disk write + live re-scan) ───────────────────────────────


async def commit_skill_write(token: str, *, registry: Optional[object] = None) -> Dict[str, Any]:
    """Consume a one-time token and ONLY THEN write the scaffolded skill to disk + re-scan.

    Returns an error and writes nothing if the token is missing/expired/foreign. This is the
    sole filesystem-touching path; the token check is its first action, so an ungated write
    is impossible.
    """
    from src.tools.filesystem.file_manager import consume_approval

    plan = consume_approval(token)
    if not plan:
        return {"success": False, "error": f"Invalid or expired approval token {token!r}. Nothing was written."}
    if plan.get("kind") != _TOKEN_PLAN_KIND:
        return {"success": False, "error": "Token does not authorize a skill write. Nothing was written."}

    skill_name = plan["skill_name"]
    target_dir = Path(plan["target_dir"])
    files: Dict[str, str] = plan["files"]

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for rel, content in files.items():
            path = target_dir / rel
            path.write_text(content, encoding="utf-8")
            written.append(str(path))
        logger.warning(f"[ CAP_BUILD ] Wrote scaffolded skill {skill_name!r}: {written}")
    except Exception as e:
        logger.error(f"[ CAP_BUILD ] Failed to write skill {skill_name!r}: {e}")
        return {"success": False, "error": f"Write failed: {e}"}

    rescanned = await _rescan_skills(plan.get("skills_root", SKILLS_ROOT), registry)
    return {
        "success": True,
        "skill_name": skill_name,
        "written": written,
        "rescanned": rescanned,
        "result": f"Skill {skill_name!r} written and {'re-scanned (now discoverable)' if rescanned else 'written (re-scan skipped — no live registry)'}.",
    }


async def _rescan_skills(skills_root: str, registry: Optional[object]) -> bool:
    """Re-scan src/skills so a freshly-written skill is discoverable without a restart.

    Uses the live registry if given, else the gap tracker's captured one. Best-effort —
    a re-scan failure never undoes the (approved) write. See module docstring for the
    already-imported-module caveat (matters only when overwriting an existing skill)."""
    if registry is None:
        try:
            from src.core.intelligence.capability_gaps import CapabilityGapTracker
            registry = CapabilityGapTracker._registry
        except Exception:
            registry = None
    if registry is None:
        logger.info("[ CAP_BUILD ] No live registry available; skipping post-write re-scan.")
        return False
    try:
        from src.infrastructure.tools.discovery.skill_scanner import SkillScanner
        SkillScanner(registry).scan_directory(skills_root)
        return True
    except Exception as e:
        logger.error(f"[ CAP_BUILD ] Post-write skill re-scan failed: {e}")
        return False
