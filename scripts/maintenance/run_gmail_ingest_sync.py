"""One-shot Gmail → personal_index sync (local-first).

By default this is incremental (respects the gmail watermark). Use
``--reset-watermark`` only when you intentionally want lookback_days to apply
from scratch — that still does not delete existing rows (hash dedupe).

Usage (from repo root):
  set PYTHONPATH=.
  set CHAD_HOME=<main checkout with Google token>
  python scripts/maintenance/run_gmail_ingest_sync.py
  python scripts/maintenance/run_gmail_ingest_sync.py --reset-watermark
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset-watermark",
        action="store_true",
        help="Clear gmail sync watermark so memory.gmail_lookback_days applies cold.",
    )
    args = parser.parse_args()

    from src.shared.config_manager import ConfigManager
    from src.core.intelligence.memory_engine import MemoryEngine
    from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager, SYNC_STATE_KEY
    from src.core.intelligence.ingestion.google_workspace import GmailIngestor
    from src.infrastructure.repositories.state_repository import StateRepository
    from src.infrastructure.database.local_store import get_local_db_path

    ConfigManager.load_config()
    print("memory.storage=", ConfigManager.get("memory.storage", "local"), flush=True)
    print("gmail_lookback_days=", ConfigManager.get("memory.gmail_lookback_days", 90), flush=True)
    print("gmail_max_results=", ConfigManager.get("memory.gmail_max_results", 2000), flush=True)

    engine = MemoryEngine()
    print("memory_backend=", type(engine.repository._backend).__name__, flush=True)
    print("local_db=", get_local_db_path(), flush=True)

    state = StateRepository()
    if args.reset_watermark:
        sync = state.get_value(SYNC_STATE_KEY, default={}) or {}
        sync.pop("gmail", None)
        state.set_value(SYNC_STATE_KEY, sync)
        print("Cleared gmail watermark; starting lookback sync...", flush=True)
    else:
        print("Keeping gmail watermark (incremental). Pass --reset-watermark for cold lookback.", flush=True)

    mgr = IngestionSyncManager(engine)
    mgr.register_ingestor(GmailIngestor())

    t0 = time.time()
    report = mgr.run_sync_cycle()
    print(json.dumps(report, indent=2, default=str), flush=True)
    print(f"Done in {time.time() - t0:.0f}s", flush=True)
    gmail = (report.get("sources") or {}).get("gmail") or {}
    return 0 if gmail.get("status") in ("success", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
