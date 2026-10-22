import pytest
from playwright.async_api import async_playwright

from src.core.orchestration.agents.browser_capture_workflow import INSPECT_LAST_MESSAGE
from src.tools.web.browser_capture_text import CAPTURE_FUNCTION


@pytest.mark.asyncio
async def test_actual_dom_inspection_selects_only_last_body_and_rejects_streaming():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<button aria-label="Scroll to bottom" aria-hidden="true">Down</button>
              <div style="height:100px;overflow-y:auto" id="panel">
              <model-response><div class="markdown">Old response</div></model-response>
              <p style="height:500px">Earlier conversation</p>
              <model-response><div class="markdown" aria-busy="false" style="white-space:pre-wrap">Exact last response.\n\n🙏</div><button>Copy</button></model-response></div>''')
            info = await page.evaluate(INSPECT_LAST_MESSAGE)
            assert not info['at_bottom'] and info['jump_target'] is None
            await page.locator('#panel').evaluate('(e)=>e.scrollTop=e.scrollHeight')
            info = await page.evaluate(INSPECT_LAST_MESSAGE)
            assert info['at_bottom'] and info['completed'] and info['response_count'] == 2
            value = await page.locator(info['body_target'][4:]).evaluate(CAPTURE_FUNCTION)
            assert value['text'] == 'Exact last response.\n\n🙏'
            await page.locator('model-response .markdown').last.evaluate('(e)=>e.setAttribute("aria-busy","true")')
            assert not (await page.evaluate(INSPECT_LAST_MESSAGE))['completed']
        finally:
            await browser.close()
