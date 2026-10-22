import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.orchestration import approval_store


class TestApprovalStore:

    def setup_method(self):
        # Each module-level store is a plain dict; clear it between tests so
        # cases don't leak tokens into each other.
        approval_store._pending.clear()

    def test_register_and_find_pending(self):
        token = approval_store.mint_token()
        approval_store.register(token, {"tool_name": "t1", "conversation_id": "c1"})

        found = approval_store.find_pending("t1", "c1")
        assert found is not None
        found_token, entry = found
        assert found_token == token
        assert entry["resolved"] is False

    def test_find_pending_scoped_by_tool_and_conversation(self):
        token = approval_store.mint_token()
        approval_store.register(token, {"tool_name": "t1", "conversation_id": "c1"})

        assert approval_store.find_pending("t1", "c2") is None
        assert approval_store.find_pending("t2", "c1") is None
        assert approval_store.find_pending("t1", "c1") is not None

    def test_resolve_marks_resolved_without_popping(self):
        token = approval_store.mint_token()
        approval_store.register(token, {"tool_name": "t1", "conversation_id": "c1"})

        assert approval_store.resolve(token, "approved") is True

        found = approval_store.find_pending("t1", "c1")
        assert found is not None
        _, entry = found
        assert entry["resolved"] is True
        assert entry["decision"] == "approved"

    def test_resolve_unknown_token_returns_false(self):
        assert approval_store.resolve("CHAD-NOPE", "approved") is False

    def test_consume_pops_payload_once(self):
        token = approval_store.mint_token()
        payload = {"tool_name": "t1", "conversation_id": "c1", "prompt": "Allow?"}
        approval_store.register(token, payload)

        consumed = approval_store.consume(token)
        assert consumed == payload

        # Second consume is a no-op (one-time token)
        assert approval_store.consume(token) is None
        assert approval_store.find_pending("t1", "c1") is None

    def test_expired_entry_is_dropped(self):
        token = approval_store.mint_token()
        approval_store.register(token, {"tool_name": "t1", "conversation_id": "c1"}, expires_minutes=10)

        # Force expiry without sleeping the test
        approval_store._pending[token]["expires_at"] = datetime.now() - timedelta(seconds=1)

        assert approval_store.find_pending("t1", "c1") is None
        assert approval_store.consume(token) is None
        assert approval_store.resolve(token, "approved") is False
