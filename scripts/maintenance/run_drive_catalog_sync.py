"""One-shot Drive catalog sync with progress logs.

Usage (from repo root):
  set PYTHONPATH=.
  set CHAD_HOME=<main checkout with Google token>
  python scripts/maintenance/run_drive_catalog_sync.py
"""

from __future__ import annotations

import json
import sys
import time


def main() -> int:
    from src.shared.config_manager import ConfigManager
    from src.core.intelligence.memory_engine import MemoryEngine
    from src.core.intelligence.ingestion.sync_manager import IngestionSyncManager, SYNC_STATE_KEY
    from src.core.intelligence.ingestion.drive_catalog import DriveCatalogIngestor
    from src.infrastructure.repositories.state_repository import StateRepository

    ConfigManager.load_config()
    print(
        "embedding_strategy=",
        ConfigManager.get("memory.embedding_strategy"),
        flush=True,
    )
    print(
        "local_embedding_model=",
        ConfigManager.get("memory.local_embedding_model"),
        flush=True,
    )
    print(
        "drive_catalog.enabled=",
        ConfigManager.get("drive_catalog.enabled"),
        flush=True,
    )
    print(
        "drive_catalog.storage=",
        ConfigManager.get("drive_catalog.storage", "local"),
        flush=True,
    )
    print(
        "drive_catalog.skip_videos=",
        ConfigManager.get("drive_catalog.skip_videos", True),
        flush=True,
    )

    engine = MemoryEngine()
    from src.infrastructure.database.local_store import get_local_db_path

    print("catalog_backend=", type(engine.repository.catalog_backend).__name__, flush=True)
    print("local_db=", get_local_db_path(), flush=True)
    ingestor = DriveCatalogIngestor()

    # Monkey-patch sync loop progress for this run only
    mgr = IngestionSyncManager(engine)
    mgr.register_ingestor(ingestor)

    state = StateRepository()
    sync = state.get_value(SYNC_STATE_KEY, default={}) or {}
    sync.pop("drive_catalog", None)
    state.set_value(SYNC_STATE_KEY, sync)
    print("Cleared drive_catalog watermark; starting FULL sync...", flush=True)

    t0 = time.time()
    # Instrument repository upsert to log progress
    repo = engine.repository
    orig = repo.upsert_memory_by_file_id
    counter = {"n": 0, "t": time.time()}

    def upsert_with_progress(*args, **kwargs):
        mid = orig(*args, **kwargs)
        counter["n"] += 1
        if counter["n"] == 1 or counter["n"] % 25 == 0:
            elapsed = time.time() - counter["t"]
            rate = counter["n"] / elapsed if elapsed > 0 else 0
            print(
                f"[ progress ] upserted={counter['n']} "
                f"elapsed={elapsed:.0f}s rate={rate:.2f}/s last_id={mid}",
                flush=True,
            )
        return mid

    repo.upsert_memory_by_file_id = upsert_with_progress  # type: ignore[method-assign]

    report = mgr.run_sync_cycle()
    print(json.dumps(report, indent=2, default=str), flush=True)
    print(f"Done in {time.time() - t0:.0f}s; upserts={counter['n']}", flush=True)
    return 0 if (report.get("sources") or {}).get("drive_catalog", {}).get("status") in (
        "success",
        "partial",
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
