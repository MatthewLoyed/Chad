"""A/B benchmark of Chad's production specialist graph and tool pipeline.

python -m evals.browser_progress_benchmark --variant legacy --repeats 5
No real accounts: an isolated headless browser serves fictional pages locally.
"""
import argparse
import asyncio
import hashlib
import json
import subprocess
import time
import uuid
import copy
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, AIMessage
from playwright.async_api import async_playwright

from evals.browser_progress_fixtures import CASES, SERMON, page_html, task, SNAPSHOT_JS
from src.core.orchestration.agents.specialist_graph import build_specialist_graph
from src.core.orchestration.agents import browser_controller  # freeze candidate import for this process
from src.core.orchestration.context import session_id_var, turn_id_var
from src.infrastructure.tools.execution.executor_strategy import MCPExecutor, LocalExecutor
from src.infrastructure.tools.schema.schema_builder import ToolSchemaBuilder
from src.shared.config_manager import ConfigManager
from src.shared import captured_content
from src.tools.web.browser_scroll import BrowserScrollTool
from src.tools.web.browser_capture_text import BrowserCaptureTextTool

SOURCE_FILES = ('src/core/orchestration/agents/specialist_graph.py',
    'src/core/orchestration/agents/browser_controller.py', 'src/core/orchestration/agents/browser_protocol.py',
    'src/core/orchestration/agents/browser_state.py', 'src/tools/web/browser_scroll.py',
    'src/core/orchestration/agents/browser_capture_workflow.py', 'src/tools/web/browser_refs.py',
    'src/infrastructure/tools/execution/executor_strategy.py',
    'src/tools/web/container_scroll.py', 'evals/browser_progress_benchmark.py', 'evals/browser_progress_fixtures.py')
STARTUP_SOURCES = {name: Path(name).read_bytes() for name in SOURCE_FILES}


class RecordingModel:
    def __init__(self, inner, metrics=None, schemas=None):
        self.inner = inner
        self.metrics = metrics if metrics is not None else {'model_calls': 0, 'peak_context_chars': 0}
        self.schemas = schemas or []

    def bind_tools(self, tools):
        return RecordingModel(self.inner.bind_tools(tools), self.metrics, tools)

    async def ainvoke(self, messages, config=None):
        from src.infrastructure.tools.schema.schema_builder import parameters_for_llm_from_tool
        schemas = [parameters_for_llm_from_tool(t) if not isinstance(t, dict) else t for t in self.schemas]
        chars = sum(len(str(m.content)) for m in messages) + len(json.dumps(schemas))
        self.metrics['peak_context_chars'] = max(self.metrics['peak_context_chars'], chars)
        self.metrics['model_calls'] += 1
        return await self.inner.ainvoke(messages, config=config)


class FixtureServer:
    name = 'playwright'
    def __init__(self, page, case):
        self.page, self.case = page, case
        self.refs, self.sequence, self.calls = {}, 0, []
        self.expire_next, self.expired_once = False, False
        self.logical_actions, self.seen_states = [], set()

    async def evidence_state(self):
        items = await self.page.evaluate(SNAPSHOT_JS)
        state = {'url': self.page.url, 'items': [{k: v for k, v in item.items() if k != 'key'} for item in items]}
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    async def snapshot(self):
        items = await self.page.evaluate(SNAPSHOT_JS)
        self.refs = {}
        lines = [f'### Page\n- Page URL: {self.page.url}']
        for item in items:
            self.sequence += 1
            ref = f'e{self.sequence}'
            if item['key']:
                self.refs[ref] = f'[data-key="{item["key"]}"]'
            role = {'button': 'button', 'input': 'textbox', 'h1': 'heading', 'h2': 'heading'}.get(item['tag'], 'generic')
            text = item['text'].replace('\n', ' / ')
            lines.append(f'- {role} "{text}" [ref={ref}]' + (' [scrollable]' if item['scroll'] else ''))
        return '\n'.join(lines)

    async def call_tool(self, name, args):
        self.calls.append({'name': name, 'args': args})
        if name == 'browser_navigate':
            if args['url'].split('/')[2] != 'fixture.test':
                raise ValueError('Benchmark navigation is restricted to fixture.test')
            await self.page.goto(args['url'])
        elif name == 'browser_snapshot':
            pass
        elif name == 'browser_evaluate':
            target = args.get('target')
            if target:
                if self.expire_next:
                    self.expire_next, self.expired_once, self.refs = False, True, {}
                    raise ValueError('Page rerendered; stale ref. Acquire a fresh snapshot.')
                selector = target[4:] if target.startswith('css=') else self.refs.get(target)
                if not selector:
                    raise ValueError('Stale element ref; inspect the current snapshot')
                value = await self.page.locator(selector).evaluate(args['function'])
            else:
                value = await self.page.evaluate(args['function'])
            return [{'type': 'text', 'text': '### Result\n' + (value if isinstance(value, str) else json.dumps(value))}]
        elif name in ('browser_click', 'browser_type'):
            selector = self.refs.get(args['target'])
            if not selector:
                raise ValueError('Stale ref; use browser_snapshot')
            if name == 'browser_click':
                await self.page.locator(selector).click(timeout=1500)
                if self.case == 'stale' and not self.expired_once:
                    self.expire_next = True
            else:
                await self.page.locator(selector).fill(args['text'])
        elif name == 'browser_wait_for':
            await asyncio.sleep(min(float(args.get('time', 0.1)), 0.5))
        elif name == 'browser_find':
            snapshot = await self.snapshot()
            matches = [line for line in snapshot.splitlines() if args['text'].lower() in line.lower()]
            return [{'type': 'text', 'text': '\n'.join(matches) or 'No matching visible text.'}]
        else:
            raise ValueError(f'Unsupported fixture tool: {name}')
        return [{'type': 'text', 'text': await self.snapshot()}]


def tools_for(server, variant='candidate'):
    registry = SimpleNamespace(_tools={}, _tool_sources={}, _categories={}, _event_bus=None,
                               mcp_registry={'playwright': server})
    mcp, local = MCPExecutor(), LocalExecutor()
    async def execute(name, args):
        before = await server.evidence_state()
        server.seen_states.add(before)
        tool = registry._tools[name]
        executor = mcp if isinstance(tool, dict) else local
        result = await executor.execute(tool, args)
        after = await server.evidence_state()
        verified_capture = False
        if name == 'browser_capture_text' and result.get('success'):
            verified_capture = captured_content.load_captured_text(result['content_id'])['text'] == SERMON
        productive = verified_capture or (after != before and after not in server.seen_states)
        server.logical_actions.append({'name': name, 'before': before, 'after': after,
                                       'productive': productive, 'verified_capture': verified_capture})
        server.seen_states.add(after)
        return result
    registry.execute_tool = execute
    for name in ('browser_navigate', 'browser_snapshot', 'browser_click', 'browser_type', 'browser_wait_for', 'browser_find', 'browser_evaluate'):
        schema = json.loads(Path(f'mcp/playwright/{name}.json').read_text(encoding='utf-8'))
        registry._tools[name] = {**schema, 'mcp_server': server, 'schema': schema}
    registry._tools['browser_scroll'] = BrowserScrollTool(registry)
    registry._tools['browser_capture_text'] = BrowserCaptureTextTool(registry)
    builder = ToolSchemaBuilder(registry)
    tools = [builder.get_langchain_tool_by_name(name) for name in registry._tools]
    if variant == 'legacy':
        from src.infrastructure.tools.schema.schema_builder import parameters_for_llm_from_tool, attach_parameters_schema
        import copy
        for entry in tools:
            if entry.name == 'browser_scroll':
                schema = copy.deepcopy(parameters_for_llm_from_tool(entry))
                schema['properties'].pop('target', None)
                attach_parameters_schema(entry, schema)
    return tools


async def run(args):
    from src.core.intelligence.lc_wrapper import ChatChad
    from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    from config.model_registry import get_profile
    ConfigManager.load_config()
    ConfigManager._config.setdefault('tools', {})['browser_approval_mode'] = 'allow_all'
    ConfigManager._config.setdefault('orchestration', {}).update(
        browser_agent_max_steps=args.max_steps, browser_controller_enabled=args.variant == 'candidate')
    from src.infrastructure.repositories.prompt_registry import PromptRegistry
    await PromptRegistry.get_instance().warmup()
    inner_model = ChatChad(adapter=LiteLLMAdapter(model_name=args.model, base_url=args.base_url,
        api_key='lm-studio', profile=get_profile(args.model)), temperature=0, thinking_enabled=False)
    prompt = args.legacy_prompt_file.read_text(encoding='utf-8') if args.variant == 'legacy' and args.legacy_prompt_file else SystemPromptBuilder.build(base_instruction='', scope='browser_agent')
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = {'model': args.model, 'base_url': args.base_url, 'temperature': 0,
                'thinking_enabled': False, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'variant': args.variant, 'max_steps': args.max_steps,
                'warm_policy': 'resident model; no unload between alternating variants',
                'fixture_charset': 'utf-8',
                'revision': subprocess.check_output(['C:/Program Files/Git/cmd/git.exe', 'rev-parse', 'HEAD'], text=True).strip()}
    try:
        with urllib.request.urlopen(args.base_url.replace('/v1', '/api/v0/models'), timeout=3) as response:
            models = json.load(response).get('data', [])
        metadata['model_runtime'] = [{k: m.get(k) for k in ('id', 'state', 'quantization', 'max_context_length', 'loaded_context_length')} for m in models if m.get('id') == args.model]
    except Exception:
        metadata['model_runtime'] = None
    metadata['source_sha256'] = {name: hashlib.sha256(body).hexdigest() for name, body in STARTUP_SOURCES.items()}
    for name, body in STARTUP_SOURCES.items():
        path = args.output / 'sources' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    (args.output / 'manifest.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    (args.output / 'effective-prompt.txt').write_text(prompt, encoding='utf-8')
    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for repeat in range(args.repeats):
                for case in args.cases:
                    context = await browser.new_context()
                    page = await context.new_page()
                    await page.route('http://fixture.test/**', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=page_html(case)))
                    server = FixtureServer(page, case)
                    tools = tools_for(server, args.variant)
                    model = RecordingModel(inner_model)
                    from src.infrastructure.tools.schema.schema_builder import parameters_for_llm_from_tool
                    (args.output / 'tools.json').write_text(json.dumps([{'name': t.name, 'parameters': parameters_for_llm_from_tool(t)} for t in tools], indent=2), encoding='utf-8')
                    graph = build_specialist_graph(model, tools, prompt, name='browser_agent')
                    sid = f'benchmark-{uuid.uuid4().hex}'
                    token, turn = session_id_var.set(sid), turn_id_var.set(sid)
                    started = time.monotonic()
                    try:
                        result = await asyncio.wait_for(graph.ainvoke({'messages': [HumanMessage(content=task(case))]},
                            config={'recursion_limit': args.max_steps * 3 + 10}), args.timeout)
                        messages = result.get('messages', [])
                        captures = []
                        for path in captured_content._root().glob('capture_*.json'):
                            try:
                                record = captured_content.load_captured_text(path.stem)
                                captures.append(record['text'])
                            except ValueError:
                                pass
                        final = str(messages[-1].content) if messages else ''
                        completed = ('CHAD-4729' in final) if case == 'short' else (SERMON in captures)
                        blocked = case == 'missing' and not completed and any(word in final.lower() for word in ('blocked', 'absent', 'not available', 'not found'))
                        usage = [m.response_metadata.get('token_usage', {}) for m in messages if isinstance(m, AIMessage)]
                        controller = result.get('browser_progress')
                        claimed = controller.get('status') == 'completed' if controller else bool(__import__('re').search(r'(?:has been captured|captured verbatim|content_id\*\*|successfully captured)', final, __import__('re').I))
                        row = {'variant': args.variant, 'case': case, 'repeat': repeat, 'completed': completed, 'correctly_blocked': blocked,
                               'false_success': claimed and not completed, **model.metrics,
                               'elapsed_s': round(time.monotonic()-started, 3), 'mcp_calls': len(server.calls),
                               'logical_actions': sum(len(m.tool_calls) for m in messages if isinstance(m, AIMessage)),
                               'unproductive_cycles': sum(not a['productive'] for a in server.logical_actions),
                               'action_observations': server.logical_actions,
                               'usage': usage, 'final': final, 'calls': server.calls,
                               'controller': controller}
                    except Exception as exc:
                        row = {'variant': args.variant, 'case': case, 'repeat': repeat, 'error': str(exc), 'completed': False,
                               'elapsed_s': round(time.monotonic()-started, 3), 'mcp_calls': len(server.calls)}
                    finally:
                        session_id_var.reset(token)
                        turn_id_var.reset(turn)
                        await context.close()
                    results.append(row)
                    (args.output / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
                    print(json.dumps({k:v for k,v in row.items() if k not in ('calls','usage','final','controller','action_observations') }), flush=True)
        finally:
            await browser.close()
    return results


async def compare(args):
    combined = []
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    for repeat in range(args.repeats):
        for index, case in enumerate(args.cases):
            variants = ['legacy', 'candidate'] if (repeat + index) % 2 == 0 else ['candidate', 'legacy']
            for variant in variants:
                trial = copy.copy(args)
                trial.variant, trial.cases, trial.repeats = variant, [case], 1
                trial.output = root / f'{repeat}-{case}-{variant}'
                rows = await run(trial)
                for row in rows:
                    row.update(repeat=repeat, variant=variant)
                combined.extend(rows)
                (root / 'comparison.json').write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--variant', choices=['legacy', 'candidate', 'both'], default='legacy')
    parser.add_argument('--model', default='prism-ml/bonsai-27b')
    parser.add_argument('--base-url', default='http://localhost:1234/v1')
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--max-steps', type=int, default=12)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--output', type=Path, default=Path('artifacts/browser-progress/legacy'))
    parser.add_argument('--legacy-prompt-file', type=Path)
    arguments = parser.parse_args()
    if arguments.variant == 'both' and not arguments.legacy_prompt_file:
        parser.error('--variant both requires --legacy-prompt-file to preserve the original effective baseline prompt')
    asyncio.run(compare(arguments) if arguments.variant == 'both' else run(arguments))
