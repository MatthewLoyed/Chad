"""
PromptRegistry — the in-memory persona cache every system prompt is built from.

Prompts resolve from local files via `persona_store` (issue #589):

    prompts/persona/<id>.md    baseline generic persona, git-tracked
    config/persona/<id>.md     this user's overlay, gitignored, wins per id

Historically this warmed up from Supabase's `persona_config` table, with a disk
cache and a bundled-defaults module behind it as fallbacks. That made the cloud
the source of truth for prompts, which meant a fresh clone ran a knowingly
different (thinner) persona than the one being developed and evaluated against,
and left no way to change a prompt without a network round-trip. Reads are now
local files only — there is no fallback chain left to get out of order, because
there is nothing to fall back from.

Supabase is no longer consulted here at all. Users who want their persona backed
up or synced across devices can push the overlay to it separately; nothing on the
read path depends on it being configured or reachable.
"""

import asyncio
from typing import Dict, List

from src.shared.logger import setup_logger
from src.infrastructure.repositories import persona_store

logger = setup_logger("PromptRegistry")


class PromptRegistry:
    """Process-wide cache of the effective persona fragments."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PromptRegistry, cls).__new__(cls, *args, **kwargs)
            cls._instance._prompts = {}
            cls._instance._scopes = {}
            cls._instance._environment = "dev"
            cls._instance._loaded = False
        return cls._instance

    @classmethod
    def get_instance(cls) -> "PromptRegistry":
        return cls()

    def set_environment(self, environment: str) -> None:
        """Set the environment label and force the next warmup to re-read from disk.

        Kept for API compatibility: prompts are no longer partitioned by environment
        now that they are files in the working tree (the checkout *is* the
        environment). The label is retained for logging and for callers that still
        pass it.
        """
        self._environment = environment
        self._loaded = False

    async def warmup(self) -> None:
        """Load the effective persona from disk into memory.

        Raises if no fragments resolve at all — that means the committed baseline is
        missing from the install, which is a broken deployment rather than a
        degraded-but-usable state worth limping along in.
        """
        logger.info("[ REGISTRY ] Warming up prompts from local persona files.")
        fragments = await asyncio.to_thread(persona_store.load_fragments)

        if not fragments:
            raise RuntimeError(
                "PromptRegistry warmup found no persona fragments. Expected the "
                f"committed baseline at {persona_store.baseline_dir()} — if this is a "
                "source checkout the directory is missing; if this is a packaged build "
                "it was not bundled."
            )

        self._prompts = {pid: content for pid, (_scope, content) in fragments.items()}
        self._scopes = {pid: scope for pid, (scope, _content) in fragments.items()}
        self._loaded = True

        customized = persona_store.overlay_ids()
        logger.info(
            "[ REGISTRY ] Loaded %d prompts (%d customized in %s).",
            len(self._prompts), len(customized), persona_store.overlay_dir(),
        )

    def get_prompt(self, prompt_id: str) -> str:
        """Retrieve a cached prompt by its ID. Raises a RuntimeError if missing or empty."""
        if not self._loaded:
            raise RuntimeError(f"PromptRegistry is not warmed up. Cannot fetch prompt: '{prompt_id}'")

        prompt = self._prompts.get(prompt_id)
        if not prompt or not prompt.strip():
            raise RuntimeError(
                f"Persona fragment '{prompt_id}' is missing or empty. Expected "
                f"{persona_store.baseline_dir() / (prompt_id + '.md')} "
                f"(or an override in {persona_store.overlay_dir()})."
            )

        return prompt

    def get_prompt_scope(self, prompt_id: str) -> str:
        """Retrieve the scope of a cached prompt."""
        if not self._loaded:
            raise RuntimeError(f"PromptRegistry is not warmed up. Cannot fetch scope for: '{prompt_id}'")
        return self._scopes.get(prompt_id, "always")

    def get_prompts_by_scopes(self, scopes: List[str]) -> Dict[str, str]:
        """Retrieve all active prompts matching any of the specified scopes."""
        if not self._loaded:
            raise RuntimeError("PromptRegistry is not warmed up. Cannot retrieve prompts by scope.")

        return {
            pid: content
            for pid, content in self._prompts.items()
            if self._scopes.get(pid, "always") in scopes
        }
