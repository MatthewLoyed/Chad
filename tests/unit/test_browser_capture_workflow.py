import json
import pytest
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage

from src.core.orchestration.agents.browser_capture_workflow import next_capture_action
from src.core.orchestration.agents.browser_state import new_progress, normalize_observation, action_signature
from src.tools.web.browser_refs import is_browser_ref
from src.core.orchestration.agents.browser_controller import build_browser_graph
from src.shared import captured_content
from src.core.orchestration.context import session_id_var


@pytest.fixture(autouse=True)
def conversation_scope():
    token = session_id_var.set('last-message-workflow-test')
    yield
    session_id_var.reset(token)

URL = 'https://gemini.google.com/app/example123'
TOOLS = dict.fromkeys(['browser_navigate', 'browser_click', 'browser_evaluate', 'browser_capture_text', 'browser_wait_for'])


def test_known_last_message_workflow_advances_from_arrow_to_verified_body():
    p = new_progress(f'Open {URL} and capture the last assistant response verbatim')
    assert next_capture_action(p, TOOLS)['arguments']['url'] == URL
    p['observation'] = '[1] button "Scroll to bottom" -> target="f5e104"'
    assert next_capture_action(p, TOOLS)['arguments']['target'] == 'f5e104'
    assert next_capture_action(p, TOOLS)['action'] == 'browser_evaluate'
    p['last_result'] = '### Result\n' + json.dumps({'chad_last_message': True, 'url': URL, 'at_bottom': True,
        'completed': True, 'body_target': 'css=model-response >> nth=-1 >> css=.markdown'})
    assert next_capture_action(p, TOOLS)['action'] == 'browser_capture_text'


def test_wrong_page_or_unfinished_body_never_captures_and_wait_is_bounded():
    p = new_progress(f'Capture the last response from {URL} verbatim')
    for _ in range(4):
        p['capture_phase'] = 'read_inspection'
        p['last_result'] = '### Result\n' + json.dumps({'chad_last_message': True, 'url': URL + 'other',
            'at_bottom': True, 'completed': True, 'body_target': 'css=.markdown'})
        decision = next_capture_action(p, TOOLS)
        assert decision['action'] != 'browser_capture_text'
    assert decision['action'] == 'browser_finish' and decision['arguments']['status'] == 'incomplete'


def test_unsupported_site_is_not_given_a_gemini_script():
    assert next_capture_action(new_progress('Capture last response from https://example.org verbatim'), TOOLS) is None


def test_frame_refs_and_renewed_set_of_mark_refs_are_supported_and_normalized():
    for ref in ['e42', 'f5e611']:
        assert is_browser_ref(ref)
    assert not is_browser_ref('document.body')
    old, new = 'button "Copy" -> target="f5e611"', 'button "Copy" -> target="f10e724"'
    assert normalize_observation(old) == normalize_observation(new)
    assert action_signature('browser_click', {'target': 'f5e611'}, old) == action_signature('browser_click', {'target': 'f10e724'}, new)


@pytest.mark.asyncio
async def test_real_graph_runs_capture_workflow_without_model_decisions(monkeypatch, tmp_path):
    monkeypatch.setattr(captured_content, '_root', lambda: tmp_path)
    calls = []
    @tool
    async def browser_navigate(url: str) -> dict:
        """Open the supplied URL."""
        calls.append('open')
        return {'success': True, 'result': [{'text': f'### Page\nPage URL: {url}\nbutton "Scroll to bottom" [ref=f5e104]'}]}
    @tool
    async def browser_click(target: str) -> dict:
        """Click the current target."""
        assert target == 'f5e104'
        calls.append('jump')
        return {'success': True, 'result': [{'text': f'### Page\nPage URL: {URL}\nFinal response'}]}
    @tool
    async def browser_evaluate(function: str) -> dict:
        """Inspect the current page."""
        calls.append('verify')
        return {'success': True, 'result': [{'text': '### Result\n' + json.dumps({'chad_last_message': True,
            'url': URL, 'at_bottom': True, 'completed': True,
            'body_target': 'css=model-response >> nth=-1 >> css=.markdown'})}]}
    @tool
    async def browser_capture_text(target: str) -> dict:
        """Capture exact body text."""
        calls.append('capture')
        return {'success': True, **captured_content.save_captured_text('Exact last message. 🙏', URL)}
    @tool
    async def browser_wait_for(time: float) -> dict:
        """Wait for content."""
        raise AssertionError('No wait needed')
    class NoModel:
        def bind_tools(self, schemas):
            raise AssertionError('Known workflow must not need a model decision')
    graph = build_browser_graph(NoModel(), [browser_navigate, browser_click, browser_evaluate,
                                          browser_capture_text, browser_wait_for], '')
    result = await graph.ainvoke({'messages': [HumanMessage(content=f'Capture the last response from {URL} verbatim')]})
    p = result['browser_progress']
    assert p['status'] == 'completed', p['reason']
    assert calls == ['open', 'jump', 'verify', 'capture']
    assert captured_content.load_captured_text(p['content_refs'][0])['text'] == 'Exact last message. 🙏'
