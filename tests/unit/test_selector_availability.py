from types import SimpleNamespace
from src.infrastructure.tools.selection import model_availability as cache


def client():
    return SimpleNamespace(_base_url="local",_model_id="qwen",_strict_model=True,_context_length=32,
        _resolved_model_id=None,last_select_meta={},_server_reachable=None)


def test_shared_cache_keeps_model_identity_and_retries_clear_residency(monkeypatch):
    cache._entries.clear();calls=[]
    monkeypatch.setattr(cache.ConfigManager,"get",staticmethod(lambda *args:5))
    def refresh(c):calls.append(c);c._resolved_model_id="qwen";return True
    monkeypatch.setattr(cache,"refresh",refresh)
    first=client();second=client()
    assert cache.ensure_available(first) and cache.ensure_available(second)
    assert len(calls)==1 and second._resolved_model_id=="qwen"
    second._resolved_model_id=None
    assert cache.ensure_available(second) and len(calls)==2
    second.last_select_meta={"status":"failed"}
    cache.invalidate_failed(second)
    assert not cache._entries


def test_disabled_cache_keeps_existing_refresh_path(monkeypatch):
    cache._entries.clear();calls=[]
    monkeypatch.setattr(cache.ConfigManager,"get",staticmethod(lambda *args:0))
    monkeypatch.setattr(cache,"refresh",lambda c:calls.append(c) or True)
    assert cache.ensure_available(client()) and cache.ensure_available(client())
    assert len(calls)==2
