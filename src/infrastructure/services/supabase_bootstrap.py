"""Optional Supabase upgrade tier — settings UI + one-click bootstrap (issue #403)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from src.shared.logger import setup_logger
from src.shared.paths import get_project_root

logger = setup_logger("SupabaseBootstrap")

_SUPABASE_ENV_KEYS = (
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_DB_URL",
)


def _env_path() -> Path:
    return get_project_root() / ".env"


def _mask_secret(value: Optional[str], visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "•" * len(value)
    return f"{value[:visible]}…{value[-visible:]}"


def get_supabase_settings() -> Dict[str, Any]:
    """Return Supabase connection status for Settings → Memory."""
    load_dotenv(_env_path(), override=True)
    url = os.getenv("SUPABASE_URL", "")
    anon = os.getenv("SUPABASE_ANON_KEY", "")
    service = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    db_url = os.getenv("SUPABASE_DB_URL", "")

    configured = bool(url and anon and db_url)
    return {
        "configured": configured,
        "url": url,
        "anon_key": _mask_secret(anon) if anon else "",
        "service_key": _mask_secret(service) if service else "",
        "db_url": _mask_secret(db_url, visible=8) if db_url else "",
        "has_service_key": bool(service),
    }


def write_supabase_env(
    *,
    url: str,
    anon_key: str,
    service_key: str,
    db_url: str,
) -> Path:
    """Persist Supabase credentials to the project `.env` file."""
    env_path = _env_path()
    updates = {
        "SUPABASE_URL": url.strip(),
        "SUPABASE_ANON_KEY": anon_key.strip(),
        "SUPABASE_SERVICE_KEY": (service_key or anon_key).strip(),
        "SUPABASE_DB_URL": db_url.strip(),
    }

    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            new_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated.add(key)
        else:
            new_lines.append(line)

    missing = [k for k in _SUPABASE_ENV_KEYS if k not in updated and updates.get(k)]
    if missing:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# Supabase (optional upgrade — issue #403)")
        for key in missing:
            new_lines.append(f"{key}={updates[key]}")

    env_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    logger.info(f"[ SUPABASE ] Wrote credentials to {env_path}")
    return env_path


def reload_supabase_manager():
    """Re-read `.env` and rebuild the SupabaseManager singleton."""
    from src.infrastructure.database.supabase_manager import SupabaseManager

    load_dotenv(_env_path(), override=True)
    existing = SupabaseManager._instance
    if existing is not None:
        client = getattr(existing, "_client", None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        existing._initialized = False
        SupabaseManager._instance = None

    from src.infrastructure.database.supabase_manager import get_supabase_manager

    return get_supabase_manager()


async def _rewarm_prompt_registry() -> None:
    from src.infrastructure.repositories.prompt_registry import PromptRegistry

    registry = PromptRegistry.get_instance()
    registry._loaded = False
    await registry.warmup()


async def run_bootstrap(
    *,
    url: str,
    anon_key: str,
    service_key: str,
    db_url: str,
) -> Dict[str, Any]:
    """
    Save credentials, provision schema/persona/tool-RAG, and re-warm prompts.

    Mirrors scripts/setup/bootstrap_supabase.py but callable from the GUI.
    """
    missing = [
        label
        for label, val in (
            ("Project URL", url),
            ("Anon key", anon_key),
            ("Database URL", db_url),
        )
        if not (val or "").strip()
    ]
    if missing:
        return {"success": False, "error": f"Missing required field(s): {', '.join(missing)}"}

    try:
        write_supabase_env(
            url=url,
            anon_key=anon_key,
            service_key=service_key or anon_key,
            db_url=db_url,
        )
        load_dotenv(_env_path(), override=True)
        reload_supabase_manager()

        from scripts.setup.apply_migrations import apply_all
        from src.scripts.refresh_rag_tools import refresh_supabase_tools

        steps: list[str] = []

        logger.info("[ SUPABASE ] Step 1/2: applying schema migrations")
        await asyncio.to_thread(apply_all, db_url.strip())
        steps.append("Schema migrations applied")

        # No persona step: prompts are local files for every tier (#589) and are
        # never seeded to or read from Supabase.

        logger.info("[ SUPABASE ] Step 2/2: syncing tool RAG")
        await refresh_supabase_tools()
        steps.append("Tool RAG synced")

        await _rewarm_prompt_registry()
        steps.append("Prompt cache refreshed")

        mgr = reload_supabase_manager()
        if not mgr.enabled:
            return {
                "success": False,
                "error": "Credentials saved but Supabase client failed to initialize. Check your keys.",
                "steps": steps,
            }

        return {
            "success": True,
            "message": "Supabase connected and provisioned. Restart the Brain if tool RAG does not pick up immediately.",
            "steps": steps,
        }
    except Exception as e:
        logger.error(f"[ SUPABASE ] Bootstrap failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
