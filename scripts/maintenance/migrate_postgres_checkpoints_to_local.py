"""One-shot migration: copy existing Postgres/Supabase LangGraph checkpoints into the
local sqlite checkpointer, now that langgraph.checkpointer_storage defaults to 'local'
(see langgraph_engine.py::_get_checkpointer). Read-only against Postgres; additive-only
to the local sqlite file (INSERT OR REPLACE keyed by thread/checkpoint id) — safe to re-run.

Only migrates each thread's LATEST checkpoint (the full accumulated message history via
LangGraph's messages-channel reducer) — not every intermediate step. That's all
SessionHistoryIngestor needs to index past exchanges.

Usage (from repo root):
  set PYTHONPATH=.
  python scripts/maintenance/migrate_postgres_checkpoints_to_local.py
"""

from __future__ import annotations

import os
import sys
import time
import asyncio


def _build_conn_string() -> str:
    conn_string = os.getenv("SUPABASE_DB_URL")
    if not conn_string:
        db_pass = os.getenv("SUPABASE_DB_PASSWORD", "").strip('"')
        supabase_url = os.getenv("SUPABASE_URL", "")
        project_id = supabase_url.split("//")[1].split(".")[0] if "//" in supabase_url else ""
        conn_string = f"postgresql://postgres.{project_id}:{db_pass}@aws-1-us-east-2.pooler.supabase.com:5432/postgres"
    sep = "&" if "?" in conn_string else "?"
    conn_string += f"{sep}keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"
    return conn_string


async def _migrate() -> dict:
    import aiosqlite
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    local_store_path = os.getenv("CHAD_LOCAL_CHECKPOINT_DB_PATH") or os.path.join(
        "data", "local_langgraph_checkpoints.db"
    )
    os.makedirs(os.path.dirname(local_store_path) or ".", exist_ok=True)

    conn_string = _build_conn_string()
    migrated = 0
    skipped = 0
    errors = 0
    thread_ids: list = []

    async with AsyncPostgresSaver.from_conn_string(conn_string) as pg_saver:
        async with pg_saver.conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT thread_id FROM checkpoints")
            rows = await cur.fetchall()
            thread_ids = [r["thread_id"] for r in rows]
        print(f"Found {len(thread_ids)} threads in Postgres.", flush=True)

        async with aiosqlite.connect(local_store_path) as local_conn:
            local_saver = AsyncSqliteSaver(local_conn)
            await local_saver.setup()

            for thread_id in thread_ids:
                try:
                    tup = await pg_saver.aget_tuple(
                        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                    )
                    if not tup or not tup.checkpoint:
                        skipped += 1
                        continue
                    local_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                    await local_saver.aput(local_config, tup.checkpoint, tup.metadata or {}, {})
                    migrated += 1
                    if migrated == 1 or migrated % 25 == 0:
                        print(f"[ progress ] migrated={migrated}/{len(thread_ids)}", flush=True)
                except Exception as e:
                    errors += 1
                    print(f"[ WARN ] Failed thread {thread_id}: {e}", flush=True)

    return {
        "threads_found": len(thread_ids),
        "migrated": migrated,
        "skipped": skipped,
        "errors": errors,
    }


def main() -> int:
    from src.shared.config_manager import ConfigManager

    # psycopg async requires a selector loop on Windows — the default ProactorEventLoop
    # can't run it (matches the same gotcha _get_checkpointer's Postgres path warns about).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    ConfigManager.load_config()
    print(
        "langgraph.checkpointer_storage=",
        ConfigManager.get("langgraph.checkpointer_storage", "local"),
        flush=True,
    )

    t0 = time.time()
    report = asyncio.run(_migrate())
    print(report, flush=True)
    print(f"Done in {time.time() - t0:.0f}s", flush=True)
    return 0 if report["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
