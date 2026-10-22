#!/usr/bin/env python3
"""
Outcome-Aware Tool Selection (OATS) Vector Refinement Script.
Author: Antigravity

This script executes a batch process to refine tool embeddings in the Supabase database
based on execution outcomes (successes vs. failures) logged in `tool_outcome_log`.

Attraction/Repulsion Formula:
    v = (1 - alpha) * e_base + alpha * c_success - (0.5 * alpha) * c_failure
    e_refined = v / ||v||_2

Where:
    - e_base: Original tool embedding (tools.embedding)
    - c_success: Mean of successful query embeddings for this tool
    - c_failure: Mean of failed query embeddings for this tool
    - alpha: Speed of attraction / interpolation parameter (default: 0.3)
    - MIN_OUTCOMES: Minimum logged outcomes required to refine (default: 10)
"""

import os
import sys
import datetime
import argparse
import logging
import numpy as np

# Adjust sys.path to allow imports from workspace root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.infrastructure.database.supabase_manager import get_supabase_manager

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("OATSRefine")

BANNER = """
========================================================================
     🅾️ 🅰️ 🆃️ 🆂️ : Outcome-Aware Tool Selection Embedding Refinement
========================================================================
"""

def parse_args():
    parser = argparse.ArgumentParser(description="Refine tool embeddings using logged outcomes.")
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.3,
        help="Interpolation learning rate (default: 0.3)."
    )
    parser.add_argument(
        "--min-outcomes",
        type=int,
        default=10,
        help="Minimum required outcomes to trigger refinement (default: 10)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the refinement and output calculations without writing to Supabase."
    )
    parser.add_argument(
        "--tool",
        type=str,
        default=None,
        help="Name of a specific tool to refine (defaults to all)."
    )
    return parser.parse_args()

def l2_normalize(vec):
    norm = np.linalg.norm(vec)
    if norm > 0:
        return vec / norm
    return vec


def _accumulate_outcome_vectors(logs, alpha: float):
    """Split logged outcomes into weighted success/failure vectors.

    Override rules (shared by the Supabase and local-first paths so their math can't drift):
      - user_rating "negative" → repel, weight 1.0 (overrides a programmatic success)
      - user_rating "positive" → attract, weight 1.0 (overrides a programmatic failure)
      - outcome "success"      → attract, weight alpha
      - outcome "failure"      → repel, weight 0.5 * alpha
    """
    success_vectors, success_weights = [], []
    failure_vectors, failure_weights = [], []
    for log in logs:
        emb = log.get("query_embedding")
        if not emb or not isinstance(emb, list) or len(emb) != 768:
            continue
        outcome = log.get("outcome")
        user_rating = log.get("user_rating")
        arr = np.array(emb, dtype=np.float32)

        if user_rating == "negative":
            failure_vectors.append(arr)
            failure_weights.append(1.0)
        elif user_rating == "positive":
            success_vectors.append(arr)
            success_weights.append(1.0)
        elif outcome == "success":
            success_vectors.append(arr)
            success_weights.append(alpha)
        elif outcome == "failure":
            failure_vectors.append(arr)
            failure_weights.append(0.5 * alpha)

    return success_vectors, success_weights, failure_vectors, failure_weights


def _compute_refined_embedding(base_emb, logs, alpha: float):
    """Apply the attraction/repulsion formula to a single tool.

    Returns `(e_refined, total_outcomes, n_success, n_failure)` where `e_refined` is the
    L2-normalized refined vector. The caller decides whether `total_outcomes` clears the
    minimum threshold. Kept backend-agnostic so `refine_embeddings` (Supabase) and
    `_refine_embeddings_local` produce byte-identical vectors from the same inputs.
    """
    sv, sw, fv, fw = _accumulate_outcome_vectors(logs, alpha)
    total_outcomes = len(sv) + len(fv)

    v = (1.0 - alpha) * base_emb
    # Scale centroid updates: 1.0 if there is explicit user feedback, otherwise baseline alpha.
    success_coef = 1.0 if any(w == 1.0 for w in sw) else alpha
    failure_coef = 1.0 if any(w == 1.0 for w in fw) else (0.5 * alpha)

    if sv:
        v += success_coef * np.average(sv, axis=0, weights=sw)
    if fv:
        v -= failure_coef * np.average(fv, axis=0, weights=fw)

    return l2_normalize(v), total_outcomes, len(sv), len(fv)


def refine_embeddings(alpha: float = 0.3, min_outcomes: int = 10, dry_run: bool = False, tool: str = None) -> bool:
    """
    Executes the core OATS vector refinement process.
    Can be called programmatically or via CLI.
    """
    db = get_supabase_manager()
    if not db.enabled:
        # Local-first (issue #470): no Supabase → refine the local tool-RAG cache in place.
        return _refine_embeddings_local(alpha=alpha, min_outcomes=min_outcomes, dry_run=dry_run, tool=tool)

    logger.info(f"Connecting to Supabase at: {db.url}")
    logger.info(f"Refinement Parameters -> Alpha: {alpha}, Min Outcomes: {min_outcomes}, Dry Run: {dry_run}")
    
    # 1. Fetch tools
    logger.info("Fetching tools from database...")
    tools_filter = {"name": f"eq.{tool}"} if tool else None
    tools = db.query(
        "tools", 
        select="id,name,embedding,embedding_refined,oats_last_refined,oats_outcome_count",
        filters=tools_filter
    )
    
    if not tools:
        logger.warning("No tools found matching search parameters.")
        return False
        
    logger.info(f"Loaded {len(tools)} tools. Processing outcomes...")
    
    refined_count = 0
    skipped_count = 0
    
    for tool_item in tools:
        tool_name = tool_item.get("name")
        tool_id = tool_item.get("id")
        base_embedding_list = tool_item.get("embedding")
        
        if not base_embedding_list or not isinstance(base_embedding_list, list):
            logger.warning(f"Tool '{tool_name}' has no base embedding. Skipping.")
            skipped_count += 1
            continue
            
        base_emb = np.array(base_embedding_list, dtype=np.float32)
        if len(base_emb) != 768:
            logger.warning(f"Tool '{tool_name}' base embedding is not 768-dim (found {len(base_emb)}). Skipping.")
            skipped_count += 1
            continue
            
        # 2. Fetch logged outcomes for this tool
        logs = db.query(
            "tool_outcome_log",
            select="query_embedding,outcome,user_rating",
            filters={"tool_name": f"eq.{tool_name}"}
        )

        e_refined, total_outcomes, n_success, n_failure = _compute_refined_embedding(base_emb, logs, alpha)

        if total_outcomes < min_outcomes:
            logger.info(
                f"Tool '{tool_name}' has insufficient logs ({total_outcomes}/{min_outcomes} required). "
                f"Successes: {n_success}, Failures: {n_failure}. Skipping refinement."
            )
            skipped_count += 1
            continue

        # 3. Calculate attraction and repulsion centroids
        logger.info(
            f"Refining '{tool_name}' (Logs: {total_outcomes} total | {n_success} success-candidates, {n_failure} failure-candidates)"
        )

        # Calculate cosine similarity shifts for metrics
        cosine_base_refined = np.dot(base_emb, e_refined) / (np.linalg.norm(base_emb) * np.linalg.norm(e_refined))
        logger.info(f"  -> Original vs Refined Cosine Similarity: {cosine_base_refined:.4f}")
        
        # 4. Perform database update
        if dry_run:
            logger.info(f"  [DRY RUN] Would update '{tool_name}' with refined embedding.")
        else:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            update_data = {
                "embedding_refined": e_refined.tolist(),
                "oats_last_refined": now_iso,
                "oats_outcome_count": total_outcomes
            }
            success = db.update("tools", filters={"id": f"eq.{tool_id}"}, data=update_data)
            if success:
                logger.info(f"  [UPDATED] Successfully updated refined embedding for '{tool_name}' in database.")
                refined_count += 1
            else:
                logger.error(f"  [FAILED] Database write failed for '{tool_name}'.")
                
    logger.info("========================= SUMMARY =========================")
    logger.info(f"Total tools processed: {len(tools)}")
    logger.info(f"Successfully refined: {refined_count}")
    logger.info(f"Skipped (insufficient data/no base embedding): {skipped_count}")
    logger.info("OATS vector refinement step complete!")
    return True


def _refine_embeddings_local(alpha: float = 0.3, min_outcomes: int = 10, dry_run: bool = False, tool: str = None) -> bool:
    """Local-first OATS refinement (issue #470).

    Mirrors `refine_embeddings` with no Supabase: base vectors come from `local_store`'s
    `tool_embeddings` cache (the same table the offline selector searches), outcomes come
    from the `tool_outcome_log` shim table, and the refined vector is written back to
    `tool_embeddings.embedding_refined` — so `_query_local_rag`'s
    `COALESCE(embedding_refined, embedding)` picks it up on the next turn and it survives
    a Brain restart (it's on disk in `local_chad.db`).
    """
    from src.infrastructure.database import local_store
    from src.core.orchestration.oats_refiner import ensure_local_oats_tables

    ensure_local_oats_tables()  # register tool_outcome_log on the shim before querying it
    db = get_supabase_manager()  # disabled → query() routes to the local shim

    logger.info(f"Refinement Parameters -> Alpha: {alpha}, Min Outcomes: {min_outcomes}, Dry Run: {dry_run} (LOCAL)")

    # 1. Load base tool embeddings from the local RAG cache.
    conn = local_store.local_db_connect()
    try:
        if tool:
            rows = conn.execute(
                "SELECT name, embedding FROM tool_embeddings WHERE name = ?", (tool,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT name, embedding FROM tool_embeddings").fetchall()
        tool_rows = [(r["name"], r["embedding"]) for r in rows]
    finally:
        conn.close()

    if not tool_rows:
        logger.warning("No local tool embeddings found matching search parameters.")
        return False

    logger.info(f"Loaded {len(tool_rows)} local tools. Processing outcomes...")

    refined_count = 0
    skipped_count = 0
    pending_updates = []  # (name, refined_blob, total_outcomes)

    for tool_name, base_blob in tool_rows:
        if base_blob is None:
            logger.warning(f"Tool '{tool_name}' has no base embedding. Skipping.")
            skipped_count += 1
            continue

        base_emb = np.array(local_store.blob_to_embedding(base_blob), dtype=np.float32)
        if len(base_emb) != 768:
            logger.warning(f"Tool '{tool_name}' base embedding is not 768-dim (found {len(base_emb)}). Skipping.")
            skipped_count += 1
            continue

        # 2. Fetch logged outcomes for this tool from the local shim.
        logs = db.query(
            "tool_outcome_log",
            select="query_embedding,outcome,user_rating",
            filters={"tool_name": f"eq.{tool_name}"}
        )

        e_refined, total_outcomes, n_success, n_failure = _compute_refined_embedding(base_emb, logs, alpha)

        if total_outcomes < min_outcomes:
            logger.info(
                f"Tool '{tool_name}' has insufficient logs ({total_outcomes}/{min_outcomes} required). "
                f"Successes: {n_success}, Failures: {n_failure}. Skipping refinement."
            )
            skipped_count += 1
            continue

        logger.info(
            f"Refining '{tool_name}' (Logs: {total_outcomes} total | {n_success} success-candidates, {n_failure} failure-candidates)"
        )
        cosine_base_refined = np.dot(base_emb, e_refined) / (np.linalg.norm(base_emb) * np.linalg.norm(e_refined))
        logger.info(f"  -> Original vs Refined Cosine Similarity: {cosine_base_refined:.4f}")

        if dry_run:
            logger.info(f"  [DRY RUN] Would update local '{tool_name}' with refined embedding.")
        else:
            refined_blob = local_store.embedding_to_blob(e_refined.tolist())
            pending_updates.append((tool_name, refined_blob, total_outcomes))

    # 3. Persist all refined vectors in one connection.
    if pending_updates and not dry_run:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = local_store.local_db_connect()
        try:
            for tool_name, refined_blob, total_outcomes in pending_updates:
                conn.execute(
                    "UPDATE tool_embeddings SET embedding_refined = ?, oats_last_refined = ?, "
                    "oats_outcome_count = ? WHERE name = ?",
                    (refined_blob, now_iso, total_outcomes, tool_name),
                )
            conn.commit()
            refined_count = len(pending_updates)
            for tool_name, _, _ in pending_updates:
                logger.info(f"  [UPDATED] Locally refined embedding for '{tool_name}'.")
        finally:
            conn.close()

    logger.info("===================== SUMMARY (LOCAL) =====================")
    logger.info(f"Total tools processed: {len(tool_rows)}")
    logger.info(f"Successfully refined: {refined_count}")
    logger.info(f"Skipped (insufficient data/no base embedding): {skipped_count}")
    logger.info("OATS local vector refinement step complete!")
    return True


def main():
    print(BANNER)
    args = parse_args()
    success = refine_embeddings(
        alpha=args.alpha,
        min_outcomes=args.min_outcomes,
        dry_run=args.dry_run,
        tool=args.tool
    )
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()

