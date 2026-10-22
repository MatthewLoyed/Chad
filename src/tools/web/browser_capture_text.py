"""Capture a selected message body as data, outside model-visible browser output."""
import asyncio
import json
from typing import Any

from src.shared.captured_content import save_captured_text
from src.tools.base_tool import BaseTool
from src.tools.web.browser_refs import is_browser_ref

# Fixed, read-only JavaScript: callers supply an observed target, never executable code.
CAPTURE_FUNCTION = """(element) => {
    if (!element || !(element instanceof HTMLElement)) throw new Error('Select a message body element');
    if (element.closest('[aria-busy="true"]')) throw new Error('Wait until the message finishes');
    const text = element.innerText;
    return {text, char_count: Array.from(text).length, source_url: window.location.href};
}"""


def parse_capture_result(parts: list) -> dict:
    for part in parts:
        text = part.get('text', '') if isinstance(part, dict) else getattr(part, 'text', '')
        if not isinstance(text, str):
            continue
        # Playwright wraps evaluate values in "### Result", followed by code logs.
        candidate = text.lstrip()
        if candidate.startswith('### Result'):
            candidate = candidate[len('### Result'):].lstrip()
        if candidate.startswith('```json'):
            candidate = candidate[len('```json'):].lstrip()
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get('text'), str) and isinstance(value.get('source_url'), str):
            if value.get('char_count') != len(value['text']):
                raise ValueError('Browser capture was incomplete; no content was saved. Select a smaller complete message.')
            return value
    raise ValueError('Could not capture complete text. Refresh the snapshot and select the message body again.')


class BrowserCaptureTextTool(BaseTool):
    name = 'browser_capture_text'
    description = (
        'Capture the full rendered text of ONE observed element verbatim for transfer to another tool. '
        'For copying a chat response, select only the completed response body, not the conversation or toolbar. '
        'Expand collapsed content first. Returns content_id, source URL, character count and SHA-256; '
        'the body stays outside model context. Forward content_id to google_drive_create_doc without retyping.'
    )
    category = 'playwright'
    is_side_effecting = False

    def __init__(self, registry: Any, config=None):
        super().__init__(config)
        self.registry = registry

    def format_action(self, params):
        return 'Capturing exact page text...'

    def get_schema(self):
        return {'name': self.name, 'description': self.description, 'parameters': {
            'type': 'object', 'properties': {'target': {'type': 'string',
                'description': 'Exact current snapshot ref (e42 or f5e611), or css= followed by a unique selector established by DOM inspection if the body has no ref. Select the complete message body, never guess a selector.'}},
            'required': ['target'],
        }}

    async def execute(self, target: str, **kwargs):
        if not isinstance(target, str) or len(target) > 2048 or not (
            is_browser_ref(target) or (target.startswith('css=') and len(target) > 4)
        ):
            return {'success': False, 'error': 'Use a current e-number ref or css= with a unique selector established by DOM inspection.'}
        registry = getattr(self.registry, 'mcp_registry', None)
        server = registry.get('playwright') if registry else None
        if server is None:
            return {'success': False, 'error': 'Playwright MCP server is not connected.'}
        try:
            # Capture directly into storage, BEFORE MCPExecutor prunes/truncates browser
            # output or logs result text. Only this fixed read-only function is allowed.
            from src.core.orchestration.context import record_browser_mcp_call
            record_browser_mcp_call()
            parts = await asyncio.wait_for(server.call_tool('browser_evaluate', {
                'target': target, 'function': CAPTURE_FUNCTION,
            }), timeout=60)
            value = parse_capture_result(parts)
            metadata = save_captured_text(value['text'], value['source_url'])
            return {'success': True, **metadata,
                    'result': 'Text captured unchanged. Pass content_id to the destination tool; do not retype or summarize.'}
        except (ValueError, OSError, asyncio.TimeoutError) as exc:
            return {'success': False, 'error': str(exc) or 'Browser capture timed out.'}
        except Exception:
            return {'success': False, 'error': 'Browser text capture failed. Refresh the snapshot and try the current message ref.'}
