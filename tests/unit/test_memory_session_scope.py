"""MemoryEngine.session_key resolves per-turn via contextvar (issue #208).

Retrieval during a turn reads self.session_key; a single MemoryEngine instance is
shared across concurrent turns, so the scope must follow the executing turn's
session (session_id_var), not a shared attribute a parallel turn can re-point.
"""

from src.core.intelligence.memory_engine import MemoryEngine
from src.core.orchestration.context import session_id_var


def _bare_engine(default_key: str = "instance_default") -> MemoryEngine:
    """A MemoryEngine without the heavy __init__ wiring (repository/embeddings)."""
    e = object.__new__(MemoryEngine)
    e._session_key_default = default_key
    return e


def test_session_key_prefers_contextvar():
    e = _bare_engine("fallback")

    # Outside any turn: falls back to the instance default.
    assert session_id_var.get() is None
    assert e.session_key == "fallback"

    # Inside turn A's context: resolves to A.
    tok_a = session_id_var.set("sessionA")
    assert e.session_key == "sessionA"
    session_id_var.reset(tok_a)

    # Inside turn B's context: resolves to B — the same instance, no clobber.
    tok_b = session_id_var.set("sessionB")
    assert e.session_key == "sessionB"
    session_id_var.reset(tok_b)

    # Back outside a turn: fallback again.
    assert e.session_key == "fallback"


def test_setter_updates_only_the_fallback_default():
    e = _bare_engine("fallback")
    e.session_key = "new_default"
    assert e._session_key_default == "new_default"
    assert e.session_key == "new_default"

    # A concurrent turn's contextvar still wins over the (now-updated) default,
    # so a background turn re-pointing the default can't hijack an active turn's reads.
    tok = session_id_var.set("active_turn")
    assert e.session_key == "active_turn"
    session_id_var.reset(tok)
