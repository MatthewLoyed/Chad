"""
One-time migration: Supabase personal_index → local NetworkX knowledge graph.

Clears the old graph, re-seeds from the updated config/personal_knowledge.md,
then pulls all personal_index records from Supabase (Chrome history, Gmail, Drive)
and ingests them into the local graph. Supabase data is left intact.

Run with:
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe src/scripts/migrate_personal_index.py
"""

import asyncio
import logging
import os
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s]: %(message)s")
logger = logging.getLogger("Migration")

# Generic homepage visits for these domains carry no personal signal — skip them.
# Specific subpages (a GitHub repo, a Reddit thread, a university course page) are kept.
NOISE_DOMAINS = {
    "google.com", "accounts.google.com", "myaccount.google.com",
    "localhost", "127.0.0.1",
}

NOISE_PATHS = {"", "/", "/search", "/webhp", "/newtab"}


def _is_noise_chrome_entry(content: str, metadata: dict) -> bool:
    url = metadata.get("url", "")
    title = metadata.get("title", "")
    if not url:
        return not content or len(content.strip()) < 15

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.rstrip("/") or "/"

        if domain in NOISE_DOMAINS:
            return True

        if parsed.path in NOISE_PATHS and not parsed.query:
            return True

    except Exception:
        pass

    if not title or len(title.strip()) < 5:
        return True

    return False


async def migrate():
    os.environ.setdefault("PYTHONPATH", ".")

    from src.shared.paths import get_project_root
    from src.infrastructure.database.supabase_manager import get_supabase_manager
    from src.core.intelligence.graph_rag_client import GraphRAGClient

    # ── Step 1: Wipe old graph so re-seed picks up updated personal_knowledge.md ──
    graph_path = get_project_root() / "data" / "knowledge_graph.json"
    if graph_path.exists():
        logger.info(f"Removing old graph file: {graph_path}")
        graph_path.unlink()

    # ── Step 2: Initialize fresh graph (seeds from personal_knowledge.md) ──────────
    client = GraphRAGClient()
    await client.initialize()
    seed_count = client._graph.number_of_nodes()
    logger.info(f"Re-seeded from personal_knowledge.md — {seed_count} base nodes.")

    # ── Step 3: Pull Supabase personal_index records ──────────────────────────────
    supabase = get_supabase_manager()
    if not supabase.enabled:
        logger.error("Supabase not configured. Check .env for SUPABASE_URL / SUPABASE_ANON_KEY.")
        return

    page_size = 200
    offset = 0
    all_records = []

    logger.info("Fetching personal_index records from Supabase...")
    while True:
        batch = supabase.query(
            "memories",
            select="content,source,metadata,created_at",
            filters={"category": "eq.personal_index"},
            limit=page_size,
            offset=offset,
            order="created_at.asc",
        )
        if not batch:
            break
        all_records.extend(batch)
        logger.info(f"  Fetched {len(all_records)} records so far...")
        if len(batch) < page_size:
            break
        offset += page_size

    logger.info(f"Total personal_index records fetched: {len(all_records)}")

    # ── Step 4: Filter noise and prepare ingest list ───────────────────────────────
    to_ingest = []
    skip_counts = {"short": 0, "chrome_noise": 0}

    for record in all_records:
        content = (record.get("content") or "").strip()
        source = record.get("source") or "supabase_migration"
        metadata = record.get("metadata") or {}

        if len(content) < 15:
            skip_counts["short"] += 1
            continue

        # Chrome and legacy "user" source = raw browsing history, not personal facts.
        # Future: run a Qwen3.5-9b extraction pass to turn patterns into real facts.
        if source in ("chrome", "user"):
            skip_counts["chrome_noise"] += 1
            continue

        to_ingest.append({"content": content, "source": source})

    logger.info(
        f"Filtered: {len(to_ingest)} to ingest | "
        f"Skipped {skip_counts['short']} (too short) + {skip_counts['chrome_noise']} (Chrome noise)"
    )

    # ── Step 5: Batch ingest ───────────────────────────────────────────────────────
    if to_ingest:
        added = await client.batch_ingest(to_ingest)
        logger.info(f"Ingested {added} nodes from Supabase.")
    else:
        logger.info("No Supabase records to ingest (Supabase personal_index may be empty).")

    # ── Step 6: Summary ───────────────────────────────────────────────────────────
    status = client.get_status()
    logger.info("=" * 60)
    logger.info("Migration complete.")
    logger.info(f"  Total nodes : {status.get('total_facts', '?')}")
    logger.info(f"  Relationships: {status.get('relationships', '?')}")
    logger.info(f"  Topics      : {status.get('topics', {})}")
    logger.info(f"  Graph file  : {status.get('graph_file', '?')}")
    logger.info("Restart the Brain server for the new graph to take effect.")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(migrate())
