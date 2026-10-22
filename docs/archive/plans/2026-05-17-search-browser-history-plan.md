# Implementation Plan: Browser & YouTube History Search

This implementation plan outlines the exact coding tasks required to add specialized Chrome/YouTube history search and fix YouTube history link formatting.

---

## Task 1: Ingestion Layer Formatter Update
**File**: `src/core/intelligence/ingestion/chrome.py`
- Modify `chrome.py` to format YouTube items with exact URLs: `Watched YouTube Video: '{clean_title}' ({url})`.
- Modify `chrome.py` to format general web pages with exact URLs: `Visited Web Page: '{display_title}' ({url})`.

---

## Task 2: Service Layer Extensions
**File**: `src/core/intelligence/memory_engine.py`
- Add `search_browser_history(self, query: str, limit: int = 10, sort_by: str = "relevance")` method:
  - Perform Gemini vector embedding on the query.
  - Call `self.repository.vector_search_memories` with an expanded candidate size.
  - Filter results strictly for `category == 'personal_index'`.
  - Sort by recency (`created_at` timestamp descending) or relevance (`similarity` descending).
  - Deduplicate identical titles/URLs.
  - Fall back to standard keyword ILIKE query if embedding fails.
- Add `_map_and_dedup_history(self, items: List[Dict[str, Any]], limit: int)` helper.

---

## Task 3: Tool Layer Implementation
**File**: `src/tools/web/search_browser_history.py`
- Create `SearchBrowserHistoryTool` class inheriting from `BaseTool`.
- Implement `get_schema()` declaring `query` (required), `limit` (optional), and `sort_by` (optional enum).
- Implement `execute()` invoking `MemoryEngine.search_browser_history()` via `asyncio.to_thread`.

---

## Task 4: Unit Testing & Verification
**File**: `tests/unit/test_search_browser_history.py`
- Write comprehensive unit tests verifying:
  - Exact URL formatting of ingested items.
  - Relevance vs. Recency sorting order correctness.
  - Correct search result mapping and deduplication.
  - Seamless fallback when embeddings fail.
