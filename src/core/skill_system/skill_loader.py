"""
SkillLoader — manages the Skill catalog for Chad's progressive disclosure system.

Architecture:
  - Startup: loads the skill index from local ``src/skills/**/SKILL.md`` (source of truth)
  - Supabase is backup/optional only — used if the local catalog is empty
  - On-demand: reads full SKILL.md content from disk when a skill is activated
  - Catalog injection: builds a compact skill table for the supervisor's system prompt

The "Skill" primitive is STATELESS and CHEAP — it injects markdown expertise
into the LLM's context without spinning up a sub-agent or executing code.

Three-Primitive Taxonomy:
  📖 Skill (SKILL.md)  — this file manages
  🔧 Tool Call         — handled by ToolRegistry / ToolExecutor
  🤖 Subagent          — handled by handoffs.py + agents/
"""

import os
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from src.core.skill_system.skill_allowlist import ALLOWED_SKILLS
from src.shared.logger import setup_logger
from src.shared.paths import get_project_root

logger = setup_logger("SkillLoader")

# Root of the project, used to resolve relative file paths stored in Supabase
PROJECT_ROOT = get_project_root()

# Default skills directory (relative to project root)
SKILLS_DIR = PROJECT_ROOT / "src" / "skills"


import yaml

class SkillEntry:
    """Lightweight in-memory representation of a skill's catalog entry."""

    def __init__(
        self,
        name: str,
        description: str,
        file_path: str,
        version: str = "1.0.0",
        tags: Optional[List[str]] = None,
        subagent: Optional[str] = None,
        requires: Optional[str] = None,
        runtime: Optional[str] = None,
        install: Optional[str] = None,
        enabled: bool = True,
    ):
        self.name = name
        self.description = description
        self.file_path = file_path  # Relative to PROJECT_ROOT
        self.version = version
        self.tags = tags or []
        self.subagent = subagent  # Optional link to a Python sub-agent class
        self.requires = requires  # e.g. "code_execution"
        self.runtime = runtime    # e.g. "node" or "python"
        self.install = install    # e.g. "npm install docx"
        self.enabled = enabled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "file_path": self.file_path,
            "version": self.version,
            "tags": self.tags,
            "subagent": self.subagent,
            "requires": self.requires,
            "runtime": self.runtime,
            "install": self.install,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillEntry":
        return cls(
            name=data["name"],
            description=data["description"],
            file_path=data["file_path"],
            version=data.get("version", "1.0.0"),
            tags=data.get("tags") or [],
            subagent=data.get("subagent"),
            requires=data.get("requires"),
            runtime=data.get("runtime"),
            install=data.get("install"),
            enabled=data.get("enabled", True),
        )

    def to_catalog_line(self) -> str:
        """Returns a single compact line for the supervisor system prompt catalog."""
        tag_str = f" [{', '.join(self.tags)}]" if self.tags else ""
        subagent_hint = f" → delegates to `{self.subagent}`" if self.subagent else ""
        requires_hint = f" (requires: {self.requires})" if self.requires else ""
        # Truncate long descriptions to keep the catalog lean (~25 tokens per skill max)
        desc = self.description if len(self.description) <= 100 else self.description[:97] + "..."
        return f"- **{self.name}**{tag_str}: {desc}{subagent_hint}{requires_hint}"


class SkillLoader:
    """
    Manages the skill catalog and content loading with progressive disclosure.
    """

    def __init__(self):
        self._catalog: Dict[str, SkillEntry] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """
        Load skill index from local SKILL.md files (source of truth).

        Supabase is backup only — consulted only when the local catalog is empty.
        """
        if self._initialized:
            return

        self._scan_local_skills()
        source = "local SKILL.md"
        if self.is_empty:
            logger.warning(
                "[ SKILLS ] Local catalog empty — trying Supabase backup index."
            )
            loaded = await self._load_from_supabase()
            if loaded:
                source = "Supabase backup"
            else:
                logger.warning("[ SKILLS ] Supabase backup also empty/unavailable.")

        self._initialized = True
        logger.info(f"[ SKILLS ] Catalog ready: {len(self._catalog)} skills loaded.")
        try:
            from src.shared.startup_timing import set_flag
            set_flag(
                "skill_loader_path",
                f"{source} ({len(self._catalog)} skills)"
            )
        except Exception:
            pass

    async def _load_from_supabase(self) -> bool:
        """Optional backup: query the skills table. Returns True if any rows loaded."""
        try:
            import os
            import asyncio
            from supabase import create_client

            url = os.getenv("SUPABASE_URL")
            key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
            if not url or not key:
                return False

            # Wrap synchronous Postgrest/HTTP request in asyncio.to_thread with a 5.0s timeout
            logger.info("[ SKILLS ] Querying Supabase skills table (timeout=5.0s)...")
            def query_supabase():
                client = create_client(url, key)
                return client.table("skills").select("*").eq("enabled", True).execute()
                
            try:
                response = await asyncio.wait_for(asyncio.to_thread(query_supabase), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("[ SKILLS ] Supabase skills query timed out after 5.0s.")
                return False

            if not response.data:
                logger.info("[ SKILLS ] No skills found in Supabase — using local scan.")
                return False

            for row in response.data:
                entry = SkillEntry(
                    name=row["name"],
                    description=row["description"],
                    file_path=row["file_path"],
                    version=row.get("version", "1.0.0"),
                    tags=row.get("tags") or [],
                    subagent=row.get("subagent"),
                    requires=row.get("requires"),
                    runtime=row.get("runtime"),
                    install=row.get("install"),
                    enabled=row.get("enabled", True),
                )
                if not self._is_loadable(entry):
                    logger.debug(
                        f"[ SKILLS ] Skipping non-loadable catalog entry: {entry.name}"
                    )
                    continue
                self._catalog[entry.name] = entry

            logger.info(f"[ SKILLS ] Loaded {len(self._catalog)} skills from Supabase.")
            return True

        except Exception as e:
            logger.warning(f"[ SKILLS ] Supabase load failed: {e}")
            return False

    def _get_dir_mtime(self, path: Path) -> float:
        """Get the latest modification time of a directory and its SKILL.md files."""
        if not path.exists():
            return 0.0
        latest_mtime = path.stat().st_mtime
        try:
            for p in path.rglob("SKILL.md"):
                mtime = p.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
        except Exception:
            pass
        return latest_mtime

    def _load_cache(self) -> bool:
        """Try loading the catalog from the local cache file."""
        cache_file = PROJECT_ROOT / "data" / "skill_cache.json"
        if not cache_file.exists():
            return False

        try:
            import json
            with open(cache_file, "r", encoding="utf-8") as f:
                payload = json.load(f)

            cached_timestamps = payload.get("dir_timestamps", {})
            current_mtime = self._get_dir_mtime(SKILLS_DIR)
            cached_mtime = cached_timestamps.get("src/skills", 0.0)

            if current_mtime > cached_mtime:
                logger.info("[ CACHE ] Stale skill cache detected.")
                return False

            catalog_data = payload.get("catalog", {})
            for name, data in catalog_data.items():
                self._catalog[name] = SkillEntry.from_dict(data)

            logger.info(f"[ CACHE ] Loaded {len(self._catalog)} skills from local cache.")
            return True
        except Exception as e:
            logger.warning(f"[ CACHE ] Failed to load skill cache: {e}")
            return False

    def _save_cache(self) -> None:
        """Save the current catalog to the local cache file."""
        try:
            cache_file = PROJECT_ROOT / "data" / "skill_cache.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)

            import json
            import time
            timestamps = {
                "src/skills": self._get_dir_mtime(SKILLS_DIR)
            }

            catalog_data = {name: entry.to_dict() for name, entry in self._catalog.items()}

            payload = {
                "version": "1.0",
                "timestamp": time.time(),
                "dir_timestamps": timestamps,
                "catalog": catalog_data
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

            logger.info(f"[ CACHE ] Saved {len(self._catalog)} skills to cache.")
        except Exception as e:
            logger.error(f"[ CACHE ] Failed to save skill cache: {e}")

    def _scan_local_skills(self) -> None:
        """Fallback: walk src/skills/**/SKILL.md and parse YAML frontmatter (with caching)."""
        if self._load_cache():
            return

        if not SKILLS_DIR.exists():
            logger.warning(f"[ SKILLS ] Skills directory not found: {SKILLS_DIR}")
            return

        for skill_file in SKILLS_DIR.rglob("SKILL.md"):
            try:
                entry = self._parse_skill_file(skill_file)
                if entry and entry.enabled and self._is_loadable(entry):
                    self._catalog[entry.name] = entry
            except Exception as e:
                logger.warning(f"[ SKILLS ] Failed to parse {skill_file}: {e}")

        # Save to cache for subsequent boots
        self._save_cache()

    def _parse_skill_file(self, skill_file: Path) -> Optional[SkillEntry]:
        """Parse YAML frontmatter from a SKILL.md file."""
        content = skill_file.read_text(encoding="utf-8")

        # Extract frontmatter between --- delimiters
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not match:
            logger.warning(f"[ SKILLS ] No frontmatter in {skill_file}")
            return None

        frontmatter_text = match.group(1)

        try:
            data = yaml.safe_load(frontmatter_text)
        except Exception as e:
            logger.error(f"[ SKILLS ] YAML parse error in {skill_file}: {e}")
            return None

        if not data:
            return None

        name = data.get("name")
        description = data.get("description", "")
        if not name or not description:
            return None

        # Relative path for storage
        rel_path = str(skill_file.relative_to(PROJECT_ROOT)).replace("\\", "/")

        return SkillEntry(
            name=name,
            description=description,
            file_path=rel_path,
            version=str(data.get("version", "1.0.0")),
            tags=data.get("tags") or [],
            subagent=data.get("subagent"),
            requires=data.get("requires"),
            runtime=data.get("runtime"),
            install=data.get("install"),
            enabled=data.get("enabled", True),
        )

    # -------------------------------------------------------------------------
    # Intent-Based On-Demand Discovery
    # -------------------------------------------------------------------------

    SKILL_INTENT_PATTERNS = {
        "kroki": re.compile(r"\b(diagram|flowchart|plantuml|graphviz|mermaid|blockdiag|c4|architecture diagram)\b", re.I),
        "marp": re.compile(r"\b(marp|slides?|slide deck|presentation deck|markdown slides)\b", re.I),
        "presenton": re.compile(r"\b(presentation|slides?|pitch deck|slide deck|powerpoint|generate presentation)\b", re.I),
        "typst": re.compile(r"\b(typst|typeset|printable document|invoice|styled pdf|designed pdf)\b", re.I),
        "pdf": re.compile(r"\b(pdf|merge pdf|extract text from pdf|split pdf|fill form|ocr pdf)\b", re.I),
        "docx": re.compile(r"\b(docx|word document|\.docx)\b", re.I),
        "xlsx": re.compile(r"\b(xlsx|spreadsheet|excel|csv|tabular data|\.xlsx)\b", re.I),
        "pptx": re.compile(r"\b(pptx|powerpoint|\.pptx)\b", re.I),
        "canva": re.compile(r"\b(canva|social graphic|poster design|flyer design|graphic design)\b", re.I),
        "excalidraw": re.compile(r"\b(excalidraw|whiteboard|hand-drawn|sketch diagram)\b", re.I),
        "youtube_music": re.compile(r"\b(youtube music|play song|play music|put on music|chill playlist)\b", re.I),
        "finance": re.compile(r"\b(plaid|bank balance|net worth|bank account|checking account)\b", re.I),
        "financial_visualization": re.compile(r"\b(spending chart|cashflow chart|runway chart|financial chart)\b", re.I),
        "inbox_triage": re.compile(r"\b(triage inbox|triage email|catch up on email|unread email pile|clean inbox)\b", re.I),
        "homework_assistant": re.compile(r"\b(homework|essay draft|academic paper|class assignment)\b", re.I),
        "frontend-design": re.compile(
            r"\b(frontend design|design system|landing page|tailwind ui|dashboard|html page|web page|interactive artifact|timeline ui|modern ui|css styling|web ui|html artifact)\b",
            re.I,
        ),
        "web-artifacts-builder": re.compile(r"\b(react artifact|shadcn|interactive web app|multi-component artifact)\b", re.I),
        "motion_design": re.compile(
            r"\b(motion design|kinetic typography|easing curve|render animation|ui motion|css animation)\b",
            re.I,
        ),
        "animation_preview": re.compile(r"\b(animate svg|css animation preview|motion preview)\b", re.I),
        "osm-maps": re.compile(r"\b(openstreetmap|osm|directions to|nearby cafe|nearby restaurant|geocode address)\b", re.I),
        "todoist": re.compile(r"\b(todoist|todoist task|todoist project)\b", re.I),
        "notion": re.compile(r"\b(notion page|notion database|notion block)\b", re.I),
        "workflow_routine": re.compile(r"\b(morning routine|daily routine|weekly review routine|recurring routine)\b", re.I),
        "organize_folder": re.compile(r"\b(organize folder|sort downloads|clean up directory|categorize files)\b", re.I),
    }

    def find_relevant_skills(self, query: str, max_results: int = 3) -> List[SkillEntry]:
        """
        Finds skills relevant to the user query based on intent patterns and tags.
        """
        if not query or not self._catalog:
            return []

        matched_names: set = set()
        for skill_name, pattern in self.SKILL_INTENT_PATTERNS.items():
            if pattern.search(query):
                if skill_name in self._catalog:
                    matched_names.add(skill_name)

        results = [self._catalog[name] for name in matched_names if name in self._catalog]
        return results[:max_results]

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def build_catalog_prompt(
        self,
        query: Optional[str] = None,
        relevant_skills: Optional[List[str]] = None,
        full: bool = False,
    ) -> str:
        """
        Returns a skill catalog prompt for injection into the supervisor system prompt.
        
        - If `full=True` or `query is None`: returns full catalog (~1,400 tokens).
        - If `query` is provided: on-demand disclosure returning only relevant skill briefs (~50-80 tokens),
          or empty string if no skills are relevant to the query.
        """
        if not self._catalog:
            return ""

        if full or (query is None and not relevant_skills):
            lines = ["### 📖 AVAILABLE SKILLS (call `load_skill` to activate any)"]
            for entry in self._catalog.values():
                lines.append(entry.to_catalog_line())

            lines.append(
                "\nSkills are workflow guides (SKILL.md), not executable tools. "
                "Quick web fact → `web_search`; deep multi-item research → `delegate_to_deep_research_agent`. "
                "Call load_skill(skill_name) only for complex SOPs listed above."
            )
            return "\n".join(lines)

        # On-demand selective mode:
        matched_entries: Dict[str, SkillEntry] = {}
        if relevant_skills:
            for s_name in relevant_skills:
                if s_name in self._catalog:
                    matched_entries[s_name] = self._catalog[s_name]

        if query:
            found = self.find_relevant_skills(query)
            for entry in found:
                matched_entries[entry.name] = entry

        if not matched_entries:
            return ""

        lines = ["### 💡 RECOMMENDED SKILLS (call `load_skill(skill_name)` to activate full SOP)"]
        for entry in matched_entries.values():
            lines.append(entry.to_catalog_line())
        lines.append(
            "Call `load_skill` before performing complex operations matching these skills."
        )
        return "\n".join(lines)

    @staticmethod
    def _is_loadable(entry: "SkillEntry") -> bool:
        """Only expose skills that are allowlisted and have a local SKILL.md file."""
        if entry.name not in ALLOWED_SKILLS:
            return False
        skill_path = PROJECT_ROOT / entry.file_path
        return skill_path.exists()

    async def load_skill_content(self, skill_name: str) -> str:
        """
        Loads the full SKILL.md content for a given skill name.
        Called on-demand when the supervisor activates a skill.

        Returns the raw markdown content (2000–5000 tokens).
        Raises ValueError if skill is unknown.
        """
        if not self._initialized:
            await self.initialize()

        entry = self._catalog.get(skill_name)
        if not entry:
            available = list(self._catalog.keys())
            raise ValueError(
                f"Unknown skill '{skill_name}'. Available skills: {available}"
            )

        skill_path = PROJECT_ROOT / entry.file_path
        if not skill_path.exists():
            raise FileNotFoundError(
                f"SKILL.md not found at: {skill_path}. "
                f"Ensure the skill exists under src/skills/ (local is source of truth)."
            )

        content = skill_path.read_text(encoding="utf-8")
        logger.info(f"[ SKILLS ] Loaded skill '{skill_name}' ({len(content)} chars)")
        return content

    def get_entry(self, skill_name: str) -> Optional[SkillEntry]:
        """Return the catalog entry for a skill (without loading content)."""
        return self._catalog.get(skill_name)

    def list_skills(self) -> List[Dict[str, Any]]:
        """Return a list of skill catalog entries as plain dicts."""
        return [
            {
                "name": e.name,
                "description": e.description,
                "tags": e.tags,
                "subagent": e.subagent,
                "version": e.version,
            }
            for e in self._catalog.values()
        ]

    @property
    def is_empty(self) -> bool:
        return len(self._catalog) == 0


# Module-level singleton — initialized once per process
_skill_loader: Optional[SkillLoader] = None


def get_skill_loader() -> SkillLoader:
    """Return the module-level SkillLoader singleton."""
    global _skill_loader
    if _skill_loader is None:
        _skill_loader = SkillLoader()
    return _skill_loader
