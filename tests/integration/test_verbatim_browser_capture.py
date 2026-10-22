"""Real headless browser capture of one message, without external accounts or LLMs."""
import json
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from src.core.orchestration.context import session_id_var
from src.shared import captured_content as storage
from src.tools.web.browser_capture_text import BrowserCaptureTextTool


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['e42', 'css=#last'])
async def test_rendered_message_capture_excludes_other_messages_and_toolbar(tmp_path, monkeypatch, target):
    monkeypatch.setattr(storage, '_root', lambda: tmp_path)
    session = session_id_var.set('browser-transfer-test')
    text = '  Full message — café 🙏\n\n' + ('Paragraph preserved exactly.\n' * 3000) + 'Closing words.  \n'
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('<article>Earlier unwanted message</article><article id="last" style="white-space:pre-wrap"></article><button>Copy response</button>')
                await page.locator('#last').evaluate('(element, text) => { element.textContent = text; }', text)

                async def call_tool(name, arguments):
                    assert name == 'browser_evaluate'
                    assert arguments['target'] == target
                    value = await page.locator('#last').evaluate(arguments['function'])
                    return [{'type': 'text', 'text': '### Result\n' + json.dumps(value)}]

                tool = BrowserCaptureTextTool(SimpleNamespace(mcp_registry={
                    'playwright': SimpleNamespace(call_tool=call_tool),
                }))
                result = await tool.execute(target=target)
                assert result['success'], result
                assert storage.load_captured_text(result['content_id'])['text'] == text
                assert result['char_count'] == len(text)
                assert 'Paragraph preserved' not in json.dumps(result)
            finally:
                await browser.close()
    finally:
        session_id_var.reset(session)
