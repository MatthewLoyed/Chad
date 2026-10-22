"""Private dev smoke: production browser controller -> capture handle -> native Docs.

Uses ChadBrowser and existing Google authorization; no background services.
Requires an explicit conversation URL. No document is created without --create-doc.
"""
import argparse
import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from langchain_core.messages import HumanMessage

from evals.browser_playwright_probe import build_playwright_registry
from evals.browser_progress_benchmark import RecordingModel
from src.core.orchestration.agents.specialist_graph import build_specialist_graph
from src.core.orchestration.context import session_id_var, turn_id_var
from src.core.orchestration.handoffs import _clean_subagent_response
from src.infrastructure.tools.execution.executor_strategy import MCPExecutor, LocalExecutor
from src.infrastructure.tools.schema.schema_builder import ToolSchemaBuilder
from src.shared import captured_content
from src.shared.config_manager import ConfigManager
from src.tools.web.browser_scroll import BrowserScrollTool
from src.tools.web.browser_capture_text import BrowserCaptureTextTool
from src.tools.workspace.drive import GoogleDriveCreateDocTool


async def run(args):
    from config.model_registry import get_profile
    from src.core.intelligence.lc_wrapper import ChatChad
    from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    from src.infrastructure.repositories.prompt_registry import PromptRegistry
    if urlparse(args.url).hostname not in ('gemini.google.com', 'share.gemini.google'):
        raise ValueError('Use the explicitly selected Gemini conversation/share URL.')
    ConfigManager.load_config()
    ConfigManager._config.setdefault('orchestration', {}).update(
        browser_controller_enabled=True, browser_agent_max_steps=args.max_steps,
        browser_task_timeout_seconds=args.timeout)
    if args.playwright_cli:
        if not args.node:
            raise ValueError('--playwright-cli requires an explicit --node executable.')
        config = ConfigManager._config['mcp_servers']['playwright']
        package_index = next(index for index, value in enumerate(config['command']) if '@playwright/mcp' in value)
        config['command'] = [args.node, str(args.playwright_cli), *config['command'][package_index + 1:]]
    await PromptRegistry.get_instance().warmup()
    args.output.mkdir(parents=True, exist_ok=True)
    mcp_registry = await build_playwright_registry()
    sid = 'gemini-smoke-' + uuid.uuid4().hex
    session, turn = session_id_var.set(sid), turn_id_var.set(sid)
    try:
        definitions = await mcp_registry.get_all_tool_definitions(use_cache=False)
        if not definitions:
            raise RuntimeError('Playwright MCP did not connect or provide tool definitions.')
        server = mcp_registry.get('playwright')
        registry = SimpleNamespace(_tools={}, _tool_sources={}, _categories={}, _event_bus=None,
                                   mcp_registry=mcp_registry)
        mcp, local = MCPExecutor(), LocalExecutor()
        async def execute(name, arguments):
            entry = registry._tools[name]
            return await (mcp if isinstance(entry, dict) else local).execute(entry, arguments)
        registry.execute_tool = execute
        for definition in definitions:
            if definition['name'].startswith('browser_'):
                registry._tools[definition['name']] = {**definition, 'schema': definition, 'mcp_server': server}
        registry._tools['browser_scroll'] = BrowserScrollTool(registry)
        registry._tools['browser_capture_text'] = BrowserCaptureTextTool(registry)
        builder = ToolSchemaBuilder(registry)
        tools = [builder.get_langchain_tool_by_name(name) for name in registry._tools]
        model = ChatChad(adapter=LiteLLMAdapter(model_name=args.model, base_url='http://localhost:1234/v1',
            api_key='lm-studio', profile=get_profile(args.model)), temperature=0, thinking_enabled=False)
        model = RecordingModel(model)
        prompt = SystemPromptBuilder.build(base_instruction='', scope='browser_agent')
        graph = build_specialist_graph(model, tools, prompt, name='browser_agent')
        objective = (f'Open the user-selected conversation {args.url}. Locate its LAST completed assistant '
            'response, the finished dating sermon. Capture ONLY that full response body verbatim using '
            'browser_capture_text. Exclude all earlier messages, user prompts, headers and toolbar text. '
            'Expand collapsed content first if needed. Never rewrite or summarize the sermon. '
            'Use the existing signed-in session; report blocked if login is required. '
            'Success is saving the exact last message in the content store and returning its content_id. '
            'A Google Doc is not required.')
        result = await asyncio.wait_for(graph.ainvoke({'messages': [HumanMessage(content=objective)]},
            config={'recursion_limit': args.max_steps * 4 + 12}), args.timeout + 15)
        # Keep live page text and model/tool traces private, outside tracked sources.
        (args.output / 'trace.json').write_text(json.dumps(result, default=lambda value:
            value.model_dump() if hasattr(value, 'model_dump') else str(value), ensure_ascii=False, indent=2), encoding='utf-8')
        handoff = _clean_subagent_response(result.get('messages', []))
        (args.output / 'handoff.txt').write_text(handoff, encoding='utf-8')
        progress = result.get('browser_progress') or {}
        refs = progress.get('content_refs', [])
        summary = {'session_id': sid, 'browser_status': progress.get('status'), 'actions': progress.get('actions'),
                   'model_calls': model.metrics['model_calls'],
                   'recoveries': progress.get('recoveries'), 'pending_approval': bool(result.get('pending_approval')),
                   'reason': progress.get('reason'), 'content_ids': refs, 'document': None}
        if progress.get('status') == 'completed' and len(refs) == 1:
            record = captured_content.load_captured_text(refs[0])
            summary.update(char_count=record['char_count'], sha256=record['sha256'], source_url=record['source_url'])
            if refs[0] not in handoff:
                raise RuntimeError('The native Docs step did not receive the verified capture handle.')
            if args.create_doc:
                summary['document'] = await GoogleDriveCreateDocTool().execute(title=args.title, content_id=refs[0])
        (args.output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in summary.items() if k != 'reason'}, ensure_ascii=False), flush=True)
    finally:
        session_id_var.reset(session)
        turn_id_var.reset(turn)
        await mcp_registry.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--model', default='prism-ml/bonsai-27b')
    parser.add_argument('--max-steps', type=int, default=25)
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--create-doc', action='store_true')
    parser.add_argument('--playwright-cli', type=Path, help='Optional installed CLI when npx is absent from this shell.')
    parser.add_argument('--node', help='Node executable for --playwright-cli.')
    parser.add_argument('--title', default='Dating sermon — exact Gemini copy')
    parser.add_argument('--output', type=Path, default=Path('artifacts/browser-progress/live-gemini'))
    asyncio.run(run(parser.parse_args()))
