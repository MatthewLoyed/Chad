"""Verbatim browser → specialist handoff → Google Docs transfer, without an LLM."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.core.orchestration.context import domain_var, session_id_var
from src.core.orchestration.handoffs import _clean_subagent_response
from src.shared import captured_content as storage
from src.tools.web.browser_capture_text import BrowserCaptureTextTool, parse_capture_result
from src.tools.workspace.drive import GoogleDriveCreateDocTool


@pytest.fixture(autouse=True)
def isolated_capture_store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, '_root', lambda: tmp_path)
    session = session_id_var.set('fictional-sermon-conversation')
    domain = domain_var.set(None)
    yield tmp_path
    session_id_var.reset(session)
    domain_var.reset(domain)


def browser_result(text):
    return [{'type': 'text', 'text': '### Result\n' + json.dumps({
        'text': text, 'char_count': len(text), 'source_url': 'https://example.org/chat/test',
    }) + '\n### Ran Playwright code\n...'}]


def docs_tool(monkeypatch, text, readback=None):
    docs = MagicMock()
    docs.documents().create().execute.return_value = {'documentId': 'fictional-doc'}
    docs.documents().get().execute.return_value = {'body': {'content': [{
        'paragraph': {'elements': [{'textRun': {'content': text + '\n' if readback is None else readback}}]},
    }]}}
    manager = MagicMock()
    manager.get_docs_service.return_value = docs
    monkeypatch.setattr('src.tools.workspace.drive.GoogleWorkspaceManager', lambda: manager)
    docs.reset_mock()
    return GoogleDriveCreateDocTool(), docs


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['e42', 'f5e611'])
async def test_large_unicode_message_is_transferred_exactly_even_when_agent_summarizes(monkeypatch, target):
    # Far larger than ordinary browser-tool output budgets; includes boundary
    # whitespace, Unicode, repeated paragraphs and a marker inside the source.
    text = '  Heading — café 🙏\n\n' + ('Exact paragraph.\n' * 6000) + '### Result\nFinal words.  \n\n'
    server = SimpleNamespace(call_tool=AsyncMock(return_value=browser_result(text)))
    tool = BrowserCaptureTextTool(SimpleNamespace(mcp_registry={'playwright': server}))
    captured = await tool.execute(target=target)
    assert captured['success']
    assert captured['char_count'] == len(text)
    assert 'Exact paragraph' not in json.dumps(captured)
    assert storage.load_captured_text(captured['content_id'])['text'] == text

    handoff = _clean_subagent_response([
        ToolMessage(name='browser_capture_text', tool_call_id='capture', content=json.dumps(captured)),
        AIMessage(content='I found the completed message.'),
    ])
    assert captured['content_id'] in handoff
    assert 'Exact paragraph' not in handoff
    # The supervisor sees metadata only; the destination loads the stored bytes.
    doc_tool, docs = docs_tool(monkeypatch, text)
    result = await doc_tool.execute(title='Copied message', content_id=captured['content_id'])
    assert result['success'] and result['verified']
    assert result['sha256'] == captured['sha256']
    inserted = docs.documents().batchUpdate.call_args.kwargs['body']['requests'][0]['insertText']['text']
    assert inserted == text


@pytest.mark.asyncio
async def test_verification_mismatch_returns_existing_doc_without_claiming_success(monkeypatch):
    captured = storage.save_captured_text('Original wording\n\n', 'https://example.org')
    tool, docs = docs_tool(monkeypatch, '', readback='Shortened wording\n')
    result = await tool.execute(title='Copy', content_id=captured['content_id'])
    assert not result['success'] and not result['verified']
    assert result['doc_id'] == 'fictional-doc'
    assert '/fictional-doc/' in result['link']
    assert docs.documents().create.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [RuntimeError('Read unavailable'), PermissionError('Authorization expired')])
async def test_verification_read_error_keeps_link_for_recovery(monkeypatch, error):
    captured = storage.save_captured_text('Exact text', 'https://example.org')
    tool, docs = docs_tool(monkeypatch, '')
    docs.documents().get().execute.side_effect = error
    result = await tool.execute(title='Copy', content_id=captured['content_id'])
    assert not result['success'] and result['verified'] is False
    assert result['doc_id'] == 'fictional-doc'


@pytest.mark.parametrize('change', ['session', 'domain', 'expired', 'tampered'])
def test_capture_rejects_wrong_scope_expiry_and_changed_text(change, isolated_capture_store, monkeypatch):
    meta = storage.save_captured_text('Original', 'https://example.org')
    path = isolated_capture_store / (meta['content_id'] + '.json')
    if change == 'session':
        session_id_var.set('another-conversation')
    elif change == 'domain':
        domain_var.set('another-domain')
    elif change == 'expired':
        monkeypatch.setattr(storage.time, 'time', lambda: 10**12)
    else:
        record = json.loads(path.read_text(encoding='utf-8'))
        record['text'] = 'Changed!'
        path.write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(ValueError):
        storage.load_captured_text(meta['content_id'])


@pytest.mark.parametrize('content_id', ['../../secrets', 'capture_invalid', 'capture_' + '0' * 32])
def test_invalid_or_missing_reference_fails(content_id):
    with pytest.raises(ValueError):
        storage.load_captured_text(content_id)


@pytest.mark.asyncio
async def test_bad_reference_does_not_create_a_document(monkeypatch):
    tool, docs = docs_tool(monkeypatch, '')
    docs.reset_mock()
    result = await tool.execute(title='Copy', content_id='capture_' + '0' * 32)
    assert not result['success']
    docs.documents.assert_not_called()


@pytest.mark.asyncio
async def test_content_and_content_id_are_mutually_exclusive(monkeypatch):
    captured = storage.save_captured_text('Exact', 'https://example.org')
    tool, docs = docs_tool(monkeypatch, '')
    docs.reset_mock()
    result = await tool.execute(title='Copy', content='Retyped', content_id=captured['content_id'])
    assert not result['success']
    docs.documents.assert_not_called()


@pytest.mark.asyncio
async def test_existing_inline_content_creation_still_works(monkeypatch):
    tool, docs = docs_tool(monkeypatch, '')
    result = await tool.execute(title='Normal doc', content='Normal body')
    assert result['success']
    assert 'verified' not in result
    assert docs.documents().batchUpdate.call_args.kwargs['body']['requests'][0]['insertText']['text'] == 'Normal body'


@pytest.mark.asyncio
async def test_capture_rejects_selector_or_code_as_target():
    server = SimpleNamespace(call_tool=AsyncMock())
    tool = BrowserCaptureTextTool(SimpleNamespace(mcp_registry={'playwright': server}))
    assert not (await tool.execute(target='document.body'))['success']
    server.call_tool.assert_not_called()


@pytest.mark.parametrize('parts', [
    [{'text': '### Result\n{"text": "truncated'}],
    [{'text': json.dumps({'text': 'Short', 'char_count': 1000, 'source_url': 'https://example.org'})}],
    [{'text': '### Error\nStale ref'}],
])
def test_capture_never_saves_truncated_or_failed_browser_results(parts):
    with pytest.raises(ValueError):
        parse_capture_result(parts)


@pytest.mark.parametrize('prefix', ['', '[TOOL_DATA]\n'])
def test_handoff_preserves_capture_id_without_final_agent_text(prefix):
    meta = storage.save_captured_text('Exact text', 'https://example.org')
    result = _clean_subagent_response([ToolMessage(
        name='browser_capture_text', tool_call_id='capture', content=prefix + json.dumps({'success': True, **meta}),
    )])
    assert meta['content_id'] in result
    assert 'VERBATIM CONTENT REFERENCES' in result


def test_new_delegation_does_not_inherit_previous_capture_reference():
    meta = storage.save_captured_text('Earlier task body', 'https://example.org')
    result = _clean_subagent_response([
        HumanMessage(content='Earlier capture task'),
        ToolMessage(name='browser_capture_text', tool_call_id='old', content=json.dumps({'success': True, **meta})),
        AIMessage(content='Earlier capture completed.'),
        HumanMessage(content='Find a different response'),
        AIMessage(content='Blocked: the requested conversation is absent.'),
    ])
    assert meta['content_id'] not in result
    assert 'VERBATIM CONTENT REFERENCES' not in result
    assert 'Blocked' in result


@pytest.mark.asyncio
async def test_empty_message_and_missing_browser_fail_without_creating_capture(isolated_capture_store):
    disconnected = BrowserCaptureTextTool(SimpleNamespace(mcp_registry={}))
    assert not (await disconnected.execute(target='e1'))['success']
    empty = BrowserCaptureTextTool(SimpleNamespace(mcp_registry={'playwright': SimpleNamespace(
        call_tool=AsyncMock(return_value=browser_result('')),
    )}))
    assert not (await empty.execute(target='e1'))['success']
    assert not list(isolated_capture_store.glob('capture_*.json'))
