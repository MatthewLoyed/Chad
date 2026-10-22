"""
Ingestion Sync Manager
The central orchestrator for personal data sync cycles.
Manages sync state persistence, executes ingestors, performs deduplication
(hash for most sources; Drive catalog by file_id; Gmail by message_id),
and feeds results into the Memory Engine for vector embedding.
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from src.core.intelligence.memory_engine import MemoryEngine
from src.infrastructure.repositories.state_repository import StateRepository
from src.core.intelligence.ingestion.base import BaseIngestor

logger = logging.getLogger("SyncManager")

# State Key used in Supabase system_state to track sync watermarks
SYNC_STATE_KEY = "personal_search_sync_states"


class IngestionSyncManager:
    """
    Manages end-to-end sync cycles for all registered ingestors.
    Handles state tracking, deduplication, storage, and aging out old records.
    """

    def __init__(self, memory_engine: MemoryEngine, ingestors: Optional[List[BaseIngestor]] = None):
        self.memory_engine = memory_engine
        self.state_repo = StateRepository()
        self.ingestors = ingestors or []
        
        logger.info(f"[ SyncManager ] Initialized with {len(self.ingestors)} registered ingestors.")

    def register_ingestor(self, ingestor: BaseIngestor):
        """Registers a new ingestor to the sync group."""
        self.ingestors.append(ingestor)
        logger.info(f"[ SyncManager ] Registered ingestor: {ingestor.get_source_name()}")

    def run_sync_cycle(self) -> Dict[str, Any]:
        """
        Executes a full ingestion sync across all registered sources.
        Returns a status report summarizing added records and errors.
        """
        logger.info("[ SyncManager ] Starting scheduled ingestion cycle...")
        
        # 1. Load all sync timestamps from cloud state repo
        sync_states = self.state_repo.get_value(SYNC_STATE_KEY, default={})
        report = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "sources": {}
        }
        
        new_sync_states = {**sync_states}
        
        # 2. Process each ingestor
        for ingestor in self.ingestors:
            source_name = ingestor.get_source_name()
            logger.info(f"[ SyncManager ] Processing source: {source_name}")
            
            # Parse the last sync string into timezone-aware datetime
            last_sync_str = sync_states.get(source_name)
            last_sync_dt = None
            if last_sync_str:
                try:
                    last_sync_dt = datetime.fromisoformat(last_sync_str)
                except ValueError:
                    logger.warning(f"[ SyncManager ] Could not parse timestamp '{last_sync_str}' for {source_name}")

            cycle_start_time = datetime.now(timezone.utc)
            
            try:
                # A. Fetch records from source
                raw_records = ingestor.ingest(last_sync=last_sync_dt)
                
                added_count = 0
                duplicate_count = 0
                consecutive_failures = 0
                aborted_early = False
                
                # Track the high-watermark timestamp of processed items
                last_processed_timestamp = last_sync_dt
                
                # B. Deduplicate, batch-embed, commit
                from src.shared.config_manager import ConfigManager

                embed_batch_size = int(ConfigManager.get("embeddings.batch_size", 64) or 64)
                embed_batch_size = max(1, embed_batch_size)

                pending: list = []

                def _flush_pending() -> bool:
                    """Embed+persist pending records. Returns False if circuit-breaker trips."""
                    nonlocal added_count, consecutive_failures, aborted_early, last_processed_timestamp
                    if not pending:
                        return True
                    texts = [r["content"] for r in pending]
                    embeddings = self.memory_engine.get_embeddings(texts)
                    if not embeddings or len(embeddings) != len(pending):
                        logger.warning(
                            f"[ SyncManager ] Batch embed failed for {len(pending)} items; aborting source cycle."
                        )
                        consecutive_failures += 3
                        aborted_early = True
                        pending.clear()
                        return False

                    for record, embedding in zip(pending, embeddings):
                        if consecutive_failures >= 3:
                            aborted_early = True
                            pending.clear()
                            return False
                        if not embedding:
                            logger.warning(
                                "[ SyncManager ] Could not generate embedding for item. Skipping."
                            )
                            consecutive_failures += 1
                            continue
                        consecutive_failures = 0
                        meta = record["metadata"]
                        file_id = meta.get("file_id")
                        message_id = meta.get("message_id")
                        is_catalog = source_name == "drive_catalog" and bool(file_id)
                        is_gmail = source_name == "gmail" and bool(message_id)
                        if is_catalog:
                            mem_id = self.memory_engine.repository.upsert_memory_by_file_id(
                                file_id=file_id,
                                content=record["content"],
                                category="drive_catalog",
                                importance=record["importance"],
                                source=source_name,
                                embedding=embedding,
                                metadata=meta,
                            )
                        elif is_gmail:
                            mem_id = self.memory_engine.repository.upsert_memory_by_message_id(
                                message_id=message_id,
                                content=record["content"],
                                category="personal_index",
                                importance=record["importance"],
                                source=source_name,
                                embedding=embedding,
                                metadata=meta,
                            )
                        else:
                            # session_history rows are past-conversation exchanges, not raw
                            # activity — they belong in session_log (own UI section + a
                            # dedicated relevance threshold), not personal_index.
                            category = "session_log" if source_name == "session_history" else "personal_index"
                            mem_id = self.memory_engine.repository.create_memory(
                                content=record["content"],
                                category=category,
                                importance=record["importance"],
                                source=source_name,
                                embedding=embedding,
                                metadata=meta,
                            )
                        if mem_id != -1:
                            added_count += 1
                            ts = record.get("_parsed_ts")
                            if ts:
                                last_processed_timestamp = ts
                    pending.clear()
                    return not aborted_early

                for record in raw_records:
                    if consecutive_failures >= 3:
                        logger.warning(
                            f"[ SyncManager ] Breaking loop for '{source_name}' due to "
                            f"{consecutive_failures} consecutive embedding failures."
                        )
                        aborted_early = True
                        break

                    meta = record.get("metadata", {})
                    item_hash = meta.get("hash")
                    item_ts_str = meta.get("timestamp")
                    file_id = meta.get("file_id")
                    message_id = meta.get("message_id")

                    current_item_ts = None
                    if item_ts_str:
                        try:
                            current_item_ts = datetime.fromisoformat(
                                item_ts_str.replace("Z", "+00:00")
                                if isinstance(item_ts_str, str) and item_ts_str.endswith("Z")
                                else item_ts_str
                            )
                        except ValueError:
                            pass

                    is_catalog = source_name == "drive_catalog" and bool(file_id)
                    is_gmail = source_name == "gmail" and bool(message_id)

                    if is_catalog:
                        existing = self.memory_engine.repository.find_memory_by_file_id(
                            file_id, category="drive_catalog"
                        )
                        if existing:
                            existing_hash = (existing.get("metadata") or {}).get("hash")
                            if existing_hash and existing_hash == item_hash:
                                duplicate_count += 1
                                if current_item_ts:
                                    last_processed_timestamp = current_item_ts
                                continue
                    elif is_gmail:
                        existing = self.memory_engine.repository.find_memory_by_message_id(
                            message_id, category="personal_index"
                        )
                        if existing:
                            existing_hash = (existing.get("metadata") or {}).get("hash")
                            if existing_hash and existing_hash == item_hash:
                                duplicate_count += 1
                                if current_item_ts:
                                    last_processed_timestamp = current_item_ts
                                continue
                    elif self.memory_engine.repository.has_memory_with_hash(
                        item_hash, source=source_name
                    ):
                        duplicate_count += 1
                        if current_item_ts:
                            last_processed_timestamp = current_item_ts
                        continue

                    record = {**record, "metadata": meta, "_parsed_ts": current_item_ts}
                    pending.append(record)
                    if len(pending) >= embed_batch_size:
                        if not _flush_pending():
                            break

                if not aborted_early:
                    _flush_pending()
                
                logger.info(f"[ SyncManager ] {source_name} Finished: {added_count} saved, {duplicate_count} skipped duplicates. (Aborted early: {aborted_early})")
                # C. Enforce TTL / Data Decay Rules (e.g., Chrome history TTL)
                if source_name == "chrome" and not aborted_early and added_count > 0:
                    # Perform standard 30-day expiry for web history
                    success = self.memory_engine.repository.expire_memories_by_source(
                        source="chrome", 
                        max_age_days=30
                    )
                    if success:
                        logger.info("[ SyncManager ] Expired Chrome history records older than 30 days.")

                # C2. Drive catalog full-sync reconcile — drop rows for deleted files.
                # Skip when the listing was truncated so we never delete real files that
                # simply fell outside max_files.
                if (
                    source_name == "drive_catalog"
                    and not aborted_early
                    and getattr(ingestor, "was_full_sync", False)
                    and not getattr(ingestor, "was_truncated", False)
                ):
                    keep_ids = getattr(ingestor, "last_seen_file_ids", None)
                    if keep_ids is not None:
                        removed = self.memory_engine.repository.delete_source_memories_missing_file_ids(
                            source="drive_catalog",
                            keep_file_ids=keep_ids,
                            category="drive_catalog",
                        )
                        if removed:
                            logger.info(
                                f"[ SyncManager ] Reconciled drive_catalog: removed {removed} stale file(s)."
                            )

                # D. Record metrics. Watermark is append-safe:
                # - abort/failure → keep previous watermark (or last item actually processed)
                # - never jump to cycle_start_time on abort (that would skip unfetched mail)
                if aborted_early:
                    if (
                        last_processed_timestamp is not None
                        and last_processed_timestamp != last_sync_dt
                    ):
                        new_sync_states[source_name] = last_processed_timestamp.isoformat()
                        wm = last_processed_timestamp
                    else:
                        wm = last_sync_dt
                    report["sources"][source_name] = {
                        "status": "partial",
                        "added": added_count,
                        "duplicates": duplicate_count,
                        "timestamp": wm.isoformat() if wm else None,
                        "watermark_advanced": source_name in new_sync_states
                        and new_sync_states.get(source_name) != last_sync_str,
                    }
                else:
                    final_watermark = last_processed_timestamp or cycle_start_time
                    new_sync_states[source_name] = final_watermark.isoformat()
                    report["sources"][source_name] = {
                        "status": "success",
                        "added": added_count,
                        "duplicates": duplicate_count,
                        "timestamp": final_watermark.isoformat(),
                    }
                
            except Exception as e:
                logger.error(f"[ SyncManager ] Failed execution for {source_name}: {e}", exc_info=True)
                report["sources"][source_name] = {
                    "status": "failed",
                    "error": str(e)
                }
                # Do not advance watermark on hard failure — leave prior state.

        # 3. Persist updated watermarks back to cloud state repo
        self.state_repo.set_value(SYNC_STATE_KEY, new_sync_states)
        
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"[ SyncManager ] Cycle complete. Report: {report['sources']}")
        self._record_sync_run(report)
        return report

    def _record_sync_run(self, report: Dict[str, Any]) -> None:
        try:
            from src.core.monitoring.autonomous_run_log import SOURCE_INGESTION, record_run
            sources = report.get("sources") or {}
            parts = []
            total_added = 0
            any_failed = False
            for name, info in sources.items():
                if not isinstance(info, dict):
                    continue
                status = info.get("status", "unknown")
                added = int(info.get("added") or 0)
                total_added += added
                if status == "failed":
                    any_failed = True
                parts.append(f"{name}: +{added} ({status})")
            summary = "; ".join(parts) if parts else "No sources processed."
            record_run(
                SOURCE_INGESTION,
                outcome="failed" if any_failed else "ok",
                summary=summary,
                metadata={"sources": sources, "total_added": total_added},
                silent=total_added == 0 and not any_failed,
            )
        except Exception as e:
            logger.debug(f"[ SyncManager ] Run log failed (non-fatal): {e}")


async def start_background_sync_loop(memory_engine: MemoryEngine, interval_minutes: Optional[int] = None):
    """
    Autonomous background worker loop that boots the sync manager and drives periodic cycles.
    Executes blocking DB operations safely in an executor.
    """
    import asyncio
    from src.core.intelligence.ingestion.chrome import ChromeHistoryIngestor
    from src.core.intelligence.ingestion.google_workspace import GmailIngestor, GoogleDriveIngestor
    from src.core.intelligence.ingestion.drive_catalog import DriveCatalogIngestor
    from src.core.intelligence.ingestion.filesystem import FilesystemCatalogIngestor
    from src.core.intelligence.ingestion.session_history import SessionHistoryIngestor
    from src.core.intelligence.ingestion.groupme import GroupMeIngestor
    from src.core.intelligence.ingestion.outlook import OutlookIngestor

    from src.shared.config_manager import ConfigManager

    if interval_minutes is None:
        interval_minutes = int(ConfigManager.get("daemons.ingestion_sync_interval_minutes", 60) or 60)

    logger.warning(f"[ DAEMON ] [ SyncManager ] Initiating background Sync Daemon (Chrome/Docs/Drive catalog/Filesystem/Sessions/GroupMe/Outlook) - Interval: {interval_minutes}m")

    # Instantiate and configure ingestors. FilesystemCatalogIngestor no-ops internally
    # when filesystem_catalog.enabled is false or no roots are configured (issue #601),
    # so it's safe to always register. SessionHistoryIngestor similarly no-ops when the
    # local sqlite checkpoint DB doesn't exist (e.g. Postgres/Supabase checkpointer mode).
    # GroupMeIngestor no-ops when GROUPME_ACCESS_TOKEN is not configured.
    # OutlookIngestor no-ops when Outlook COM is unavailable or non-Windows.
    manager = IngestionSyncManager(memory_engine)
    manager.register_ingestor(ChromeHistoryIngestor())
    manager.register_ingestor(GmailIngestor())
    manager.register_ingestor(GoogleDriveIngestor())
    manager.register_ingestor(DriveCatalogIngestor())
    manager.register_ingestor(FilesystemCatalogIngestor())
    manager.register_ingestor(SessionHistoryIngestor())
    manager.register_ingestor(GroupMeIngestor())
    manager.register_ingestor(OutlookIngestor())
    
    # Delayed start to allow core systems and GUI to initialize gracefully without contention
    await asyncio.sleep(30) 
    
    while True:
        try:
            from src.shared.config_manager import ConfigManager
            from src.core.monitoring.daemon_status import daemon_enabled
            if not daemon_enabled("ingestion_sync", default=False):
                await asyncio.sleep(60)
                continue

            logger.info("[ DAEMON ] [ SyncManager ] Executing periodic sync turn...")
            loop = asyncio.get_running_loop()
            
            # Execute synchronous database / network queries off main event loop to ensure zero UI stuttering
            report = await loop.run_in_executor(None, manager.run_sync_cycle)
            logger.debug(f"[ DAEMON ] [ SyncManager ] Sync cycle report generated: {report}")
            
        except asyncio.CancelledError:
            logger.info("[ DAEMON ] [ SyncManager ] Sync Daemon cancel signal received. Exiting loop.")
            break
        except Exception as daemon_err:
            logger.error(f"[ DAEMON ] [ SyncManager ] Crash inside background daemon: {daemon_err}", exc_info=True)
        
        # Re-read interval each cycle so Automations UI changes apply without restart.
        interval_minutes = int(ConfigManager.get("daemons.ingestion_sync_interval_minutes", interval_minutes) or interval_minutes)
        await asyncio.sleep(max(5, interval_minutes) * 60)

