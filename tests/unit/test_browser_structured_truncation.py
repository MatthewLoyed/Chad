import json
import pytest

from langchain_core.messages import ToolMessage

from src.core.orchestration.nodes import tool_node


@pytest.mark.parametrize('result_as_text', [False, True])
def test_large_real_browser_envelope_stays_parseable_with_latest_page_tail(monkeypatch, result_as_text):
    monkeypatch.setattr(tool_node, '_max_tool_output_chars', lambda name: 2500)
    body = {'success': True, 'scroll': {'at_bottom': True, 'top': 1000}, 'result': [
        {'type': 'text', 'text': 'Page URL: https://example.org'},
        {'type': 'text', 'text': '### Interactive Elements (Set-of-Mark)\n' + 'long sidebar ' * 2000 + '\n### Pruned Snapshot\nFinal response [ref=e92]'},
    ]}
    if result_as_text:
        body['result'] = body['result'][-1]['text']
    message = ToolMessage(name='browser_scroll', tool_call_id='scroll', content='[TOOL_DATA]\n' + json.dumps(body))
    tool_node._truncate_large_tool_outputs([message])
    result = json.loads(message.content.removeprefix('[TOOL_DATA]\n'))
    assert result['success'] and result['scroll']['at_bottom']
    text = result['result'] if result_as_text else result['result'][-1]['text']
    assert 'Final response [ref=e92]' in text
    assert 'Set-of-Mark' in text
    assert len(message.content) <= 2500
