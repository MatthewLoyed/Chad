"""Scroll the observed panel (or discover the visible scroll container)."""
import json
import math
from src.tools.web.browser_refs import is_browser_ref


async def scroll_container(registry, direction='down', amount=None, target=None):
    if direction not in ('up', 'down') or (target is not None and not is_browser_ref(target)):
        return {'success': False, 'error': 'Use up/down and an observed e-number target.'}
    if amount is not None and (not isinstance(amount, (float, int)) or not math.isfinite(amount) or amount <= 0 or amount > 10000):
        return {'success': False, 'error': 'Scroll amount must be between 0 and 10000 pixels.'}
    servers = getattr(registry, 'mcp_registry', None)
    server = servers.get('playwright') if servers else None
    if server is None:
        return {'success': False, 'error': 'Playwright is not connected.'}
    from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
    executor = MCPExecutor()
    step = json.dumps(amount)
    sign = -1 if direction == 'up' else 1
    body = f"""async (element) => {{
      function scrollable(el) {{
        if (!el || !(el instanceof HTMLElement)) return false;
        const r=el.getBoundingClientRect();
        return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight &&
          /auto|scroll/.test(getComputedStyle(el).overflowY) && el.scrollHeight>el.clientHeight+2;
      }}
      let container=element;
      while(container && !scrollable(container)) container=container.parentElement;
      if (!container) {{
        const panels=[...document.querySelectorAll('*')].filter(scrollable);
        panels.sort((a,b)=>(b.clientHeight*b.clientWidth)-(a.clientHeight*a.clientWidth));
        container=panels[0]||document.scrollingElement;
      }}
      if (!container) throw new Error('No scroll container is available');
      const before=container.scrollTop;
      container.scrollBy(0, {sign} * ({step} || Math.max(100,container.clientHeight*0.8)));
      await new Promise(resolve=>setTimeout(resolve,150));
      const top=container.scrollTop,max=Math.max(0,container.scrollHeight-container.clientHeight);
      return {{container:container.id||container.getAttribute('aria-label')||container.tagName,
        before,top,max,moved:Math.abs(top-before)>2,at_top:top<=2,at_bottom:top>=max-2,
        url:location.href}};
    }}"""
    if target is None:
        body = body.replace('async (element) => {', 'async () => { const element=null;', 1)
    args = {'function': body, **({'target': target} if target else {})}
    metrics_result = await executor.execute({'name': 'browser_evaluate', 'mcp_server': server}, args)
    if not metrics_result.get('success'):
        return metrics_result
    metrics = {}
    for part in metrics_result.get('result', []):
        text = part.get('text', '') if isinstance(part, dict) else ''
        start = text.find('{')
        if start >= 0:
            try:
                metrics, _ = json.JSONDecoder().raw_decode(text[start:])
                break
            except ValueError:
                continue
    snapshot = await executor.execute({'name': 'browser_snapshot', 'mcp_server': server}, {})
    snapshot['scroll'] = metrics
    snapshot['scroll_target'] = target or 'auto'
    if metrics.get('at_bottom') or metrics.get('at_top'):
        snapshot['scroll_note'] = 'Container boundary observed. Inspect the returned content; switch strategy if it did not reveal useful evidence.'
    return snapshot
