from types import SimpleNamespace
from unittest.mock import Mock
from src.infrastructure.tools.selection import needs_selector as needs
from src.shared.config_manager import ConfigManager


def test_per_need_keeps_calendar_and_command_without_flat_reprune(monkeypatch):
    monkeypatch.setattr(needs,"extract_needs",lambda *a:[{"id":"calendar","requirement":"Calendar events","mode":"tool"},{"id":"git","requirement":"Git commit","mode":"tool"},{"id":"output","requirement":"Summarize","mode":"no_tool"}])
    selector=SimpleNamespace(select=Mock(side_effect=[["get_calendar_events"],["run_host_command"]]))
    turn=SimpleNamespace(_llm_selector=selector,_core_tool_names=set(),_selector_catalog=lambda *a,**k:{"get_calendar_events":{},"run_host_command":{}})
    selected=needs.select_per_need(turn,"dashboard",{"get_calendar_events":{}},None,None,None)
    assert selected==["get_calendar_events","run_host_command"]
    assert selector._last_select_meta["needs"][-1]["status"]=="no_tool"
    assert selector.select.call_count==2


def test_failed_need_is_explicit_and_uses_old_selector_fallback(monkeypatch):
    monkeypatch.setattr(ConfigManager,"get",staticmethod(lambda key,default=None: True if key=="tools.per_need_selection" else default))
    monkeypatch.setattr(needs,"extract_needs",lambda *a:[{"id":"calendar","requirement":"Calendar events","mode":"tool"}])
    selector=SimpleNamespace(select=Mock(side_effect=[[],["get_calendar_events"]]),_last_select_meta={})
    type_with_meta=type("Selector",(),{"last_select_meta":property(lambda self:self._last_select_meta)})
    real=type_with_meta();real.select=selector.select;real._last_select_meta={}
    turn=SimpleNamespace(_llm_selector=real,_core_tool_names=set(),_selector_catalog=lambda *a,**k:{"get_calendar_events":{}})
    assert needs.choose_tools(turn,"query",{},None,None,None)==["get_calendar_events"]
    assert real._last_select_meta["per_need_fallback"]["unresolved"]==["calendar"]


def test_external_reads_never_count_as_model_only_response():
    assert not needs.pure_response_need("Retrieve today's events from Google Calendar")
    assert not needs.pure_response_need("Obtain current system uptime")
    assert not needs.pure_response_need("Summarize a file after you read it")
    assert needs.pure_response_need("Compose a concise answer using the supplied facts")
