"""Production scroll wrapper against actual nested/lazy browser panels."""
import pytest
from playwright.async_api import async_playwright

from evals.browser_progress_benchmark import FixtureServer, tools_for
from evals.browser_progress_fixtures import SERMON, page_html
from src.core.orchestration.agents.browser_state import payload
from src.core.orchestration.context import browser_run_id_var, session_id_var
from src.shared import captured_content
from src.shared.config_manager import ConfigManager


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['nested', 'lazy'])
async def test_scroll_actual_panel_then_capture_final_message(case, tmp_path, monkeypatch):
    monkeypatch.setattr(captured_content, '_root', lambda: tmp_path)
    session, run = session_id_var.set('panel-test'), browser_run_id_var.set('new-delegation')
    original_get = ConfigManager.get
    monkeypatch.setattr('src.shared.config_manager.ConfigManager.get', lambda key, default=None:
                        'allow_all' if key == 'tools.browser_approval_mode' else original_get(key, default))
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.route('http://fixture.test/**', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=page_html(case)))
                server = FixtureServer(page, case)
                tools = {t.name: t for t in tools_for(server)}
                await tools['browser_navigate'].ainvoke({'url': 'http://fixture.test/' + case})
                open_ref = next(ref for ref, selector in server.refs.items() if selector == '[data-key="open"]')
                await tools['browser_click'].ainvoke({'target': open_ref})
                metrics = []
                for _ in range(8):
                    panel_ref = next(ref for ref, selector in server.refs.items() if selector == '[data-key="panel"]')
                    result = payload(await tools['browser_scroll'].ainvoke({'direction': 'down', 'target': panel_ref}))
                    assert result['success'], result
                    metrics.append(result['scroll'])
                    if result['scroll']['at_bottom']:
                        break
                assert metrics[-1]['at_bottom']
                assert metrics[-1]['top'] > 0
                assert await page.evaluate('window.scrollY') == 0
                capture_ref = next(ref for ref, selector in server.refs.items() if selector == '[data-key="sermon"]')
                capture = payload(await tools['browser_capture_text'].ainvoke({'target': capture_ref}))
                assert capture['success'], capture
                assert captured_content.load_captured_text(capture['content_id'])['text'] == SERMON
            finally:
                await browser.close()
    finally:
        browser_run_id_var.reset(run)
        session_id_var.reset(session)
