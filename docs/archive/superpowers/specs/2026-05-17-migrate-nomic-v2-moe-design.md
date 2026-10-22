# Design & Implementation Spec: Nomic v2 (MoE) Embedding Model Migration

This design document outlines the migration of the primary RAG embedding model from `nomic-ai/nomic-embed-text-v1.5` to `nomic-ai/nomic-embed-text-v2-moe`. It defines the database schema changes, code adjustments, threshold calibration process, and rollback procedures.

---

## 🎯 Objectives
1. **Model Upgrade:** Fully transition to `nomic-ai/nomic-embed-text-v2-moe` for denser, higher-quality semantic representation.
2. **Auditability:** Track model versions on tool embeddings via a new `embedding_model` column.
3. **Robust Calibration:** Empirically calibrate Cosine and RRF Relative Delta pruning thresholds using a 15-20 query test harness to ensure zero regression in tool selection accuracy.
4. **Zero Downtime / Rollback Safety:** Snapshot current v1.5 embeddings to a backup table before migrating.

---

## 🛠️ Proposed Architecture & Changes

### 1. Database Schema Migration (Supabase)
To support auditing and recovery, we will perform the following database operations:
*   **Backup Table Creation:** Snapshot existing tool embeddings.
    ```sql
    CREATE TABLE IF NOT EXISTS public.tools_embeddings_v1_backup AS 
    SELECT id, embedding FROM public.tools;
    ```
*   **Embedding Model Column:** Add `embedding_model` to trace which model was used for each record.
    ```sql
    ALTER TABLE public.tools ADD COLUMN IF NOT EXISTS embedding_model TEXT;
    ```

### 2. Code Changes

#### A. `src/infrastructure/tools/selection/semantic_selector.py`
*   Update default `model_name` parameter to `nomic-ai/nomic-embed-text-v2-moe`.
*   Introduce global constants for the calibrated thresholds to clean up inline hardcoding:
    ```python
    # Calibrated for nomic-embed-text-v2-moe on 2026-05-17
    # Previous v1.5 values: cosine=0.04, rrf=0.003
    COSINE_THRESHOLD = 0.05  # placeholder — will be updated with empirical calibration
    RRF_THRESHOLD = 0.004    # placeholder — will be updated with empirical calibration
    ```
*   Reference these constants inside `retrieve_relevant_tools`'s delta pruning logic:
    ```python
    if highest_score < 0.1:
        PRUNING_DELTA = RRF_THRESHOLD
        metric_name = "RRF Hybrid"
    else:
        PRUNING_DELTA = COSINE_THRESHOLD
        metric_name = "Cosine Similarity"
    ```

#### B. `src/infrastructure/tools/cloud/supabase_bridge.py`
*   Update default `model_name` parameter to `nomic-ai/nomic-embed-text-v2-moe`.
*   Modify `_prepare_payload` to populate the `embedding_model` column during tool sync upserts:
    ```python
    return {
        "name": name,
        "description": description,
        "intent_preamble": preamble,
        "description_hash": get_description_hash(description),
        "schema": schema,
        "embedding": embedding,
        "embedding_model": "nomic-v2-moe"
    }
    ```

---

## 📊 Threshold Recalibration Plan

Because `nomic-embed-text-v2-moe` produces higher density embeddings, scores will cluster tighter at the top. We will run a script `src/scripts/test_thresholds.py` extended with a comprehensive test suite of 18 representative queries covering various matching scenarios:

### Test Suite Categories
1.  **Zero-match (Strict Negative):** "hi", "what time is it" -> Expected: `[]`
2.  **Single Precise Tool (Strict Positive):**
    *   "check my git status" -> `["git_status"]`
    *   "search the web for latest news" -> `["web_search"]`
    *   "read the file at src/main.py" -> `["read_file"]`
3.  **Multi-Intent / Decomposed Queries (Complex):**
    *   "read main.py and push changes to git" -> `["read_file", "git_push_files"]`
    *   "draft and send an email to the team" -> `["draft_gmail_reply", "send_email"]`
4.  **Anti-Collision / Disambiguation:**
    *   "write a python script" -> `["execute_python"]` (MUST NOT return unrelated tools like `draft_gmail_reply`)

### Empirical Recalibration Process
1.  Verify v2 embeddings are successfully generated and uploaded for all 80+ tools.
2.  Run the test harness query-by-query.
3.  Collect raw cosine scores and RRF scores (if running via hybrid RRF search) for the top 5 results.
4.  Determine the minimum score gap between required tools and irrelevant tools.
5.  Set `COSINE_THRESHOLD` and `RRF_THRESHOLD` to the maximum possible values that pass all test cases (ensuring maximum precision while maintaining 100% recall).

---

## 🔄 Rollback Plan
If validation fails or tool selection degrades in production:
1.  Restore the original v1.5 embeddings:
    ```sql
    UPDATE public.tools t
    SET embedding = b.embedding,
        embedding_model = 'nomic-ai/nomic-embed-text-v1.5'
    FROM public.tools_embeddings_v1_backup b
    WHERE t.id = b.id;
    ```
2.  Revert model strings and thresholds in `semantic_selector.py` and `supabase_bridge.py`.
3.  Once the new thresholds are fully verified in production for 24 hours, safely drop the backup table:
    ```sql
    DROP TABLE IF EXISTS public.tools_embeddings_v1_backup;
    ```
