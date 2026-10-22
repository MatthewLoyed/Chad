"""
Local-shim parity tests for OATS vector refinement (issue #470, part of #462).

With no SUPABASE_URL configured, OATS must still close the self-improvement loop
entirely on-disk: tool outcomes log through the LocalPostgrestShim, `refine_embeddings`
refines the local `tool_embeddings` cache in place, the refined vector survives a
simulated Brain restart, and the offline selector prefers it — all without emitting a
single Supabase error.
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_store, local_postgrest
from src.infrastructure.database.supabase_manager import get_supabase_manager
from src.core.orchestration.oats_refiner import ensure_local_oats_tables
from src.core.orchestration.context import current_turn_tool_queries_var
from src.infrastructure.tools.event_wrapper import ToolEventWrapper
from scripts.oats_refine import refine_embeddings, _compute_refined_embedding

ALPHA = 0.3


@pytest.fixture(autouse=True)
def local_only(tmp_path, monkeypatch):
    """Force the disabled (no-Supabase) path onto an isolated on-disk sqlite file."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    db = tmp_path / "oats_test.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    # OATS registered its shim table against whatever instance was live at import;
    # re-register against this test's fresh shim (idempotent).
    ensure_local_oats_tables()
    yield


# --- helpers ----------------------------------------------------------------

def _unit(seed: int) -> np.ndarray:
    """Deterministic 768-dim unit vector."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(768).astype(np.float32)
    return v / np.linalg.norm(v)


def _seed_tool(name: str, base: np.ndarray) -> None:
    conn = local_store.local_db_connect()
    try:
        conn.execute(
            "INSERT INTO tool_embeddings (name, description_hash, embedding, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET embedding=excluded.embedding",
            (name, "hash", local_store.embedding_to_blob(base.tolist()), datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _log_outcome(db, tool_name: str, emb: np.ndarray, outcome: str, user_rating: str = None) -> dict:
    row = {
        "tool_name": tool_name,
        "query_text": "q",
        "query_embedding": emb.tolist(),
        "outcome": outcome,
    }
    if user_rating:
        row["user_rating"] = user_rating
    db.insert("tool_outcome_log", row)
    return row


def _read_tool_row(name: str):
    conn = local_store.local_db_connect()
    try:
        return conn.execute(
            "SELECT embedding, embedding_refined, oats_outcome_count FROM tool_embeddings WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        conn.close()


# --- tests ------------------------------------------------------------------

def test_outcomes_log_and_refine_writes_refined_locally():
    """No Supabase → outcomes log locally → refine writes embedding_refined in place."""
    db = get_supabase_manager()
    assert db.enabled is False  # sanity: we're exercising the local path

    base = _unit(1)
    _seed_tool("web_search", base)

    success_dir = _unit(2)
    logs = [_log_outcome(db, "web_search", success_dir, "success") for _ in range(12)]

    assert refine_embeddings(min_outcomes=10) is True

    row = _read_tool_row("web_search")
    assert row["embedding_refined"] is not None
    assert row["oats_outcome_count"] == 12

    # Parity: the persisted local vector matches the shared attraction/repulsion helper
    # that the Supabase path also uses — same inputs, byte-identical math.
    expected, _, _, _ = _compute_refined_embedding(base, logs, ALPHA)
    written = local_store.blob_to_embedding(row["embedding_refined"])
    assert np.allclose(written, expected, atol=1e-5)
    # Refinement actually moved the vector toward the success centroid.
    assert float(np.dot(written, success_dir)) > float(np.dot(base, success_dir))


def test_user_rating_override_repels_locally():
    """A negative user_rating overrides success and repels with weight 1.0, locally too."""
    db = get_supabase_manager()
    base = _unit(3)
    _seed_tool("shaky_tool", base)

    bad_dir = _unit(4)
    logs = [_log_outcome(db, "shaky_tool", bad_dir, "success", user_rating="negative") for _ in range(10)]

    assert refine_embeddings(min_outcomes=10) is True

    row = _read_tool_row("shaky_tool")
    written = local_store.blob_to_embedding(row["embedding_refined"])
    expected, _, _, _ = _compute_refined_embedding(base, logs, ALPHA)
    assert np.allclose(written, expected, atol=1e-5)
    # Repelled: refined vector points further from the thumbs-down direction than the base.
    assert float(np.dot(written, bad_dir)) < float(np.dot(base, bad_dir))


def test_insufficient_outcomes_skips_refinement():
    db = get_supabase_manager()
    _seed_tool("rare_tool", _unit(5))
    for _ in range(3):
        _log_outcome(db, "rare_tool", _unit(6), "success")

    assert refine_embeddings(min_outcomes=10) is True  # ran, but skipped this tool
    row = _read_tool_row("rare_tool")
    assert row["embedding_refined"] is None


def test_refined_embedding_survives_brain_restart(monkeypatch):
    """Refined vectors are on disk in local_chad.db, so a fresh process still sees them."""
    db = get_supabase_manager()
    base = _unit(7)
    _seed_tool("persist_tool", base)
    logs = [_log_outcome(db, "persist_tool", _unit(8), "success") for _ in range(11)]
    assert refine_embeddings(min_outcomes=10) is True

    # Simulate a Brain restart: drop the cached manager + shim singletons.
    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    row = _read_tool_row("persist_tool")
    assert row["embedding_refined"] is not None
    expected, _, _, _ = _compute_refined_embedding(base, logs, ALPHA)
    assert np.allclose(local_store.blob_to_embedding(row["embedding_refined"]), expected, atol=1e-5)


def test_local_selector_prefers_refined_embedding(monkeypatch):
    """`_query_local_rag` uses COALESCE(embedding_refined, embedding) — refinement is picked up."""
    from src.infrastructure.tools.selection.semantic_selector import SemanticToolSelector

    q = _unit(10)
    # Base points AWAY from the query (would be filtered by the 0.2 cosine floor);
    # only the refined vector (== q) can keep the tool in the results.
    _seed_tool("saved_by_refine", -q)
    _seed_tool("base_only", -q)

    conn = local_store.local_db_connect()
    try:
        conn.execute(
            "UPDATE tool_embeddings SET embedding_refined = ? WHERE name = ?",
            (local_store.embedding_to_blob(q.tolist()), "saved_by_refine"),
        )
        conn.commit()
    finally:
        conn.close()

    selector = SemanticToolSelector(decompose_queries=False)

    class FakeModel:
        def encode(self, text):
            return q

        def ensure_local_transformer(self):
            pass

    selector._model = FakeModel()
    monkeypatch.setattr(selector, "_ensure_model", lambda: None)
    monkeypatch.setattr(selector, "_ensure_local_tool_embeddings", lambda tools: None)

    available = {"saved_by_refine": {"description": "x"}, "base_only": {"description": "y"}}
    results = selector._query_local_rag("anything", available, k=5)
    names = [r["name"] for r in results]

    assert "saved_by_refine" in names   # refined vector rescued it
    assert "base_only" not in names     # base-only stayed below the cosine floor


@pytest.mark.asyncio
async def test_event_wrapper_logs_outcome_locally():
    """The outcome-log write path persists locally when Supabase is unconfigured."""
    query_emb = _unit(11).tolist()
    token = current_turn_tool_queries_var.set({
        "__query__": "user asked something",
        "my_tool": ("sub-query", query_emb),
    })

    async def original_executor(tool_name, kwargs):
        return "ok"

    try:
        wrapper = ToolEventWrapper.wrap_as_langchain_tool("my_tool", original_executor, MagicNone(), None)
        result = await wrapper()
        assert result == "ok"
    finally:
        current_turn_tool_queries_var.reset(token)

    # The insert runs in a fire-and-forget background task; poll rather than sleep a fixed
    # amount so this stays deterministic on slow CI runners (not a timing race).
    db = get_supabase_manager()
    rows = []
    for _ in range(150):  # up to ~3s
        rows = db.query("tool_outcome_log", select="tool_name,outcome,query_embedding",
                        filters={"tool_name": "eq.my_tool"})
        if rows:
            break
        await asyncio.sleep(0.02)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
    assert rows[0]["query_embedding"] == query_emb


@pytest.mark.asyncio
async def test_feedback_endpoint_writes_user_rating_locally():
    """The /feedback user_rating write path persists to the local shim when offline,
    respecting credit assignment (a thumbs-down stamps only the FAILED tools), and the
    rating badge is persisted on the message metadata so it survives a reload."""
    from src.remote.endpoints import _feedback_endpoint
    from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository

    # Seed a real conversation + assistant message so the rating badge has somewhere to land.
    repo = ChatHistoryRepository()
    conv = await repo.create_conversation()
    msg = await repo.append_message(conv["id"], "assistant", "here you go")
    turn_id = msg["id"]

    db = get_supabase_manager()
    db.insert("tool_outcome_log", {"tool_name": "good_tool", "query_text": "q",
              "query_embedding": _unit(20).tolist(), "outcome": "success", "turn_id": turn_id})
    db.insert("tool_outcome_log", {"tool_name": "bad_tool", "query_text": "q",
              "query_embedding": _unit(21).tolist(), "outcome": "failure", "turn_id": turn_id})

    class Req:
        def __init__(self):
            self.path_params = {"session_id": "s1", "turn_id": turn_id}
            self.app = SimpleNamespace(state=SimpleNamespace())

        async def json(self):
            return {"rating": "negative"}

    resp = await _feedback_endpoint(Req())
    assert resp.status_code == 200

    rows = db.query("tool_outcome_log", select="tool_name,outcome,user_rating",
                    filters={"turn_id": f"eq.{turn_id}"})
    by_tool = {r["tool_name"]: r["user_rating"] for r in rows}
    # Thumbs-down repels only the tool that FAILED; the successful tool is left untouched.
    assert by_tool["bad_tool"] == "negative"
    assert by_tool["good_tool"] is None

    # The rating badge persisted on the message metadata — survives a reload with no Supabase.
    messages = await repo.get_messages(conv["id"])
    rated = next(m for m in messages if m["id"] == turn_id)
    assert rated["metadata"]["user_rating"] == "negative"


@pytest.mark.asyncio
async def test_no_supabase_error_logs_when_unconfigured(caplog):
    db = get_supabase_manager()
    _seed_tool("quiet_tool", _unit(12))
    for _ in range(10):
        _log_outcome(db, "quiet_tool", _unit(13), "success")

    with caplog.at_level("ERROR"):
        refine_embeddings(min_outcomes=10)
    assert not any("Supabase" in r.message for r in caplog.records)


class MagicNone:
    """Minimal no-op event bus stand-in (publish is a no-op)."""

    def publish(self, *args, **kwargs):
        pass
