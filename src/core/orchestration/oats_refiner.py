import logging
import asyncio
from typing import Optional

logger = logging.getLogger("OATSRefiner")


def ensure_local_oats_tables() -> None:
    """Register the OATS `tool_outcome_log` table on the local PostgREST shim (issue #470).

    Idempotent (the shim de-dupes registrations), so every writer can call it before its
    first local insert. Only the outcome log needs a shim table — the `tools` catalog is
    refined in place against `local_store`'s `tool_embeddings` cache, not a shim table.
    """
    from src.infrastructure.database.local_postgrest import register_table

    register_table(
        "tool_outcome_log",
        columns={
            "id": "INTEGER",
            "tool_name": "TEXT",
            "query_text": "TEXT",
            "query_embedding": "JSON",
            "outcome": "TEXT",
            "user_rating": "TEXT",
            "turn_id": "TEXT",
            "created_at": "TEXT",
        },
        pk="id",
    )

class OATSRefiner:
    _lock = asyncio.Lock()
    _counter = 0
    _is_refining = False

    @classmethod
    async def increment_and_check(cls, alpha: float = 0.3, min_outcomes: int = 10, target_count: int = 20):
        """
        Increment the outcome counter.
        If it reaches target_count, trigger OATS refinement in a background task.
        """
        async with cls._lock:
            cls._counter += 1
            logger.info(f"[ OATS ] Logged tool outcome. Counter since last refinement: {cls._counter}/{target_count}")
            if cls._counter >= target_count:
                cls._counter = 0
                if not cls._is_refining:
                    cls._is_refining = True
                    # Launch background task so it doesn't block the caller
                    asyncio.create_task(cls._run_refinement_in_background(alpha, min_outcomes))

    @classmethod
    async def _run_refinement_in_background(cls, alpha: float, min_outcomes: int):
        """Run OATS vector refinement asynchronously in a background task."""
        try:
            logger.warning("[ OATS ] Triggering automated in-process OATS vector refinement...")
            from scripts.oats_refine import refine_embeddings
            
            # Run the refinement core function in a separate thread to avoid blocking the asyncio event loop
            await asyncio.to_thread(refine_embeddings, alpha=alpha, min_outcomes=min_outcomes, dry_run=False)
            logger.warning("[ OATS ] Automated in-process OATS vector refinement complete!")
        except Exception as e:
            logger.error(f"[ OATS ] Failed to run background vector refinement: {e}")
        finally:
            async with cls._lock:
                cls._is_refining = False
