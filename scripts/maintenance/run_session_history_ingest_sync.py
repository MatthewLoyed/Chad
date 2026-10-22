"""One-shot Session History backfill with progress logs.

Reads every thread in the local LangGraph checkpoint store (data/local_langgraph_checkpoints.db
by default) and indexes past conversation exchanges into memories (category=session_log)
so old sessions become vector-searchable. Safe to re-run — content is hash-deduped.

Usage (from repo root):
  set PYTHONPATH=.
  python scripts/maintenance/run_session_history_ingest_sync.py
"""

from __future__ import annotations

import json
import sys
import time


def main() -> int:
    from src.shared.config_manager import ConfigManager
    from src.core.intelligence.memory_engine import MemoryEngine
    from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager, SYNC_STATE_KEY
    from src.core.intelligence.ingestion.session_history import SessionHistoryIngestor
    from src.infrastructure.repositories.state_repository import StateRepository

    ConfigManager.load_config()
    print("session_history.enabled=", ConfigManager.get("session_history.enabled", True), flush=True)
    print("memory.storage=", ConfigManager.get("memory.storage", "local"), flush=True)

    engine = MemoryEngine()
    ingestor = SessionHistoryIngestor()
    print("checkpoint_db_path=", ingestor.checkpoint_db_path, flush=True)

    mgr = IngestionSyncManager(engine)
    mgr.register_ingestor(ingestor)

    # Clear the watermark so this run is a full backfill, not an incremental sync.
    state = StateRepository()
    sync = state.get_value(SYNC_STATE_KEY, default={}) or {}
    sync.pop("session_history", None)
    state.set_value(SYNC_STATE_KEY, sync)
    print("Cleared session_history watermark; starting FULL backfill...", flush=True)
    print(
        "Note: caps at 800 items/thread-30-exchanges per cycle — re-run this script "
        "(or wait for the periodic daemon) if the report below is 'partial'.",
        flush=True,
    )

    t0 = time.time()
    report = mgr.run_sync_cycle()
    print(json.dumps(report, indent=2, default=str), flush=True)
    print(f"Done in {time.time() - t0:.0f}s", flush=True)

    status = (report.get("sources") or {}).get("session_history", {}).get("status")
    return 0 if status in ("success", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
