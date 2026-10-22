"""
Unit tests for LocalPostgrestShim (issue #465).

Cover: every filter operator, `or=()`, multi-column order, limit/offset,
upsert-on-conflict, a full insert->query->update->delete round-trip, and proof
that the enabled (Supabase-configured) SupabaseManager path never touches the
shim.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.infrastructure.database import local_store, local_postgrest
from src.infrastructure.database.local_postgrest import LocalPostgrestShim


@pytest.fixture
def shim(tmp_path, monkeypatch):
    """A fresh shim over an isolated on-disk sqlite file with a `widgets` table."""
    db = tmp_path / "test_local.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)

    s = LocalPostgrestShim()
    s.register_table(
        "widgets",
        columns={
            "id": "INTEGER",
            "name": "TEXT",
            "qty": "INTEGER",
            "price": "REAL",
            "tag": "TEXT",
            "meta": "JSON",
        },
        pk="id",
    )
    return s


def _seed(shim):
    rows = [
        {"name": "alpha", "qty": 5, "price": 1.5, "tag": "red", "meta": {"k": "v1"}},
        {"name": "beta", "qty": 10, "price": 2.0, "tag": "blue", "meta": {"k": "v2"}},
        {"name": "gamma", "qty": 15, "price": 3.5, "tag": None, "meta": {"k": "v3"}},
        {"name": "Delta", "qty": 20, "price": 4.0, "tag": "red", "meta": {"k": "v4"}},
    ]
    return [shim.insert("widgets", r) for r in rows]


# --- round-trip -------------------------------------------------------------

def test_insert_query_update_delete_roundtrip(shim):
    inserted = shim.insert("widgets", {"name": "solo", "qty": 7, "price": 9.9, "tag": "x", "meta": {"a": 1}})
    assert inserted is not None
    assert inserted["id"] == 1
    assert inserted["name"] == "solo"
    assert inserted["meta"] == {"a": 1}  # JSON round-trips to a dict

    got = shim.query("widgets", filters={"id": "eq.1"})
    assert len(got) == 1
    assert got[0]["name"] == "solo"

    assert shim.update("widgets", {"id": "eq.1"}, {"name": "solo2", "qty": 8}) is True
    got = shim.query("widgets", filters={"id": "eq.1"})
    assert got[0]["name"] == "solo2"
    assert got[0]["qty"] == 8

    assert shim.delete("widgets", {"id": "eq.1"}) is True
    assert shim.query("widgets", filters={"id": "eq.1"}) == []


# --- operators --------------------------------------------------------------

def test_operator_eq(shim):
    _seed(shim)
    res = shim.query("widgets", filters={"name": "eq.beta"})
    assert [r["name"] for r in res] == ["beta"]


def test_operator_neq(shim):
    _seed(shim)
    res = shim.query("widgets", filters={"tag": "neq.red"})
    # `tag IS NULL` (gamma) is not returned by `!=`, matching SQL semantics.
    assert {r["name"] for r in res} == {"beta"}


def test_operators_gt_gte_lt_lte(shim):
    _seed(shim)
    assert {r["name"] for r in shim.query("widgets", filters={"qty": "gt.10"})} == {"gamma", "Delta"}
    assert {r["name"] for r in shim.query("widgets", filters={"qty": "gte.10"})} == {"beta", "gamma", "Delta"}
    assert {r["name"] for r in shim.query("widgets", filters={"qty": "lt.10"})} == {"alpha"}
    assert {r["name"] for r in shim.query("widgets", filters={"qty": "lte.10"})} == {"alpha", "beta"}


def test_operator_is_null(shim):
    _seed(shim)
    res = shim.query("widgets", filters={"tag": "is.null"})
    assert {r["name"] for r in res} == {"gamma"}


def test_operator_ilike_case_insensitive(shim):
    _seed(shim)
    # ilike matches both 'Delta' and would match 'delta' — case-insensitive.
    res = shim.query("widgets", filters={"name": "ilike.*delta*"})
    assert {r["name"] for r in res} == {"Delta"}


def test_operator_like_case_sensitive(shim):
    _seed(shim)
    # like (GLOB) is case-sensitive: lowercase 'delta' matches nothing.
    assert shim.query("widgets", filters={"name": "like.*delta*"}) == []
    assert {r["name"] for r in shim.query("widgets", filters={"name": "like.*Delta*"})} == {"Delta"}


def test_operator_in(shim):
    _seed(shim)
    res = shim.query("widgets", filters={"name": "in.(alpha,gamma)"})
    assert {r["name"] for r in res} == {"alpha", "gamma"}
    # numeric IN relies on eager type coercion, not sqlite affinity.
    res = shim.query("widgets", filters={"qty": "in.(5,20)"})
    assert {r["name"] for r in res} == {"alpha", "Delta"}


def test_or_clause(shim):
    _seed(shim)
    res = shim.query("widgets", filters={"or": "(name.eq.alpha,tag.is.null)"})
    assert {r["name"] for r in res} == {"alpha", "gamma"}


def test_and_of_filter_plus_or(shim):
    _seed(shim)
    res = shim.query(
        "widgets",
        filters={"qty": "gte.10", "or": "(tag.eq.red,tag.is.null)"},
    )
    # qty>=10 AND (tag=red OR tag IS NULL) -> gamma (null), Delta (red)
    assert {r["name"] for r in res} == {"gamma", "Delta"}


# --- ordering / paging ------------------------------------------------------

def test_multi_column_order(shim):
    _seed(shim)
    shim.insert("widgets", {"name": "epsilon", "qty": 5, "price": 0.5, "tag": "red"})
    res = shim.query("widgets", order="qty.asc,price.desc")
    names = [r["name"] for r in res]
    # qty ascending; within qty=5, price desc puts alpha(1.5) before epsilon(0.5)
    assert names[:2] == ["alpha", "epsilon"]
    assert names[-1] == "Delta"


def test_order_desc(shim):
    _seed(shim)
    res = shim.query("widgets", order="qty.desc")
    assert [r["name"] for r in res] == ["Delta", "gamma", "beta", "alpha"]


def test_limit_and_offset(shim):
    _seed(shim)
    res = shim.query("widgets", order="qty.asc", limit=2)
    assert [r["name"] for r in res] == ["alpha", "beta"]
    res = shim.query("widgets", order="qty.asc", limit=2, offset=2)
    assert [r["name"] for r in res] == ["gamma", "Delta"]
    # offset without limit still works (SQLite LIMIT -1)
    res = shim.query("widgets", order="qty.asc", offset=3)
    assert [r["name"] for r in res] == ["Delta"]


def test_select_projection(shim):
    _seed(shim)
    res = shim.query("widgets", select="name", filters={"name": "eq.beta"})
    assert res == [{"name": "beta"}]


# --- upsert -----------------------------------------------------------------

def test_upsert_insert_then_conflict_updates(shim):
    shim.register_table(
        "kv",
        columns={"name": "TEXT", "value": "TEXT", "version": "INTEGER"},
        pk="name",
    )
    first = shim.upsert("kv", {"name": "color", "value": "red", "version": 1}, on_conflict="name")
    assert first["value"] == "red"

    second = shim.upsert("kv", {"name": "color", "value": "blue", "version": 2}, on_conflict="name")
    assert second["value"] == "blue"
    assert second["version"] == 2

    all_rows = shim.query("kv")
    assert len(all_rows) == 1  # conflict updated in place, no duplicate


# --- rpc --------------------------------------------------------------------

def test_rpc_unregistered_raises(shim):
    with pytest.raises(NotImplementedError):
        shim.rpc("match_widgets", {"q": 1})


def test_rpc_registered_dispatches(shim):
    shim.register_rpc("echo", lambda params: {"got": params})
    assert shim.rpc("echo", {"a": 1}) == {"got": {"a": 1}}


# --- registration guards ----------------------------------------------------

def test_unregistered_table_raises(shim):
    with pytest.raises(KeyError):
        shim.query("does_not_exist")


def test_json_path_filter(shim):
    _seed(shim)
    res = shim.query("widgets", filters={"meta->>k": "eq.v2"})
    assert {r["name"] for r in res} == {"beta"}


# --- SupabaseManager delegation --------------------------------------------

def test_supabase_manager_disabled_delegates_to_shim(tmp_path, monkeypatch):
    """When Supabase is unconfigured, the manager routes verbs through the shim."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    db = tmp_path / "mgr.db"
    monkeypatch.setenv("CHAD_LOCAL_DB_PATH", str(db))
    monkeypatch.setattr(local_store, "_DB_PATH", None, raising=False)
    monkeypatch.setattr(local_postgrest, "_shim_instance", None, raising=False)

    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    # Prevent .env from re-supplying credentials during __init__.
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    mgr = sm_mod.SupabaseManager()
    assert mgr.enabled is False

    local_postgrest.register_table(
        "notes", columns={"id": "INTEGER", "body": "TEXT"}, pk="id"
    )
    created = mgr.insert("notes", {"body": "hello"})
    assert created["body"] == "hello"
    assert mgr.query("notes", filters={"id": "eq.1"})[0]["body"] == "hello"
    assert mgr.update("notes", {"id": "eq.1"}, {"body": "bye"}) is True
    assert mgr.query("notes", filters={"id": "eq.1"})[0]["body"] == "bye"
    assert mgr.delete("notes", {"id": "eq.1"}) is True
    assert mgr.query("notes") == []


def test_supabase_manager_enabled_path_untouched(monkeypatch):
    """With Supabase configured, the shim is never constructed or called."""
    from src.infrastructure.database import supabase_manager as sm_mod
    monkeypatch.setattr(sm_mod.SupabaseManager, "_instance", None, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setattr(sm_mod, "load_dotenv", lambda *a, **k: None)

    mgr = sm_mod.SupabaseManager()
    assert mgr.enabled is True

    # Any attempt to reach the local shim should blow up loudly.
    def _boom():
        raise AssertionError("enabled path must not touch the local shim")

    monkeypatch.setattr(sm_mod.SupabaseManager, "_local", lambda self: _boom())

    fake_response = MagicMock()
    fake_response.json.return_value = [{"id": 1, "body": "cloud"}]
    fake_response.raise_for_status.return_value = None
    mgr._client = MagicMock()
    mgr._client.get.return_value = fake_response

    res = mgr.query("notes", filters={"id": "eq.1"})
    assert res == [{"id": 1, "body": "cloud"}]
    mgr._client.get.assert_called_once()
