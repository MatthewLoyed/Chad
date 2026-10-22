"""Bounded Gemini last-response workflow using the existing gated tool executor.

No model-generated selectors or prose carry the captured body. Unsupported sites
continue through the general browser controller.
"""
import json
import re
from urllib.parse import urlparse

from src.core.orchestration.agents.browser_state import result_text

INSPECT_LAST_MESSAGE = """() => {
 const messages=[...document.querySelectorAll('model-response')];
 const last=messages.at(-1), bodies=last?[...last.querySelectorAll('.markdown')]:[];
 const body=bodies.length===1?bodies[0]:null;
 const visible=e=>!!e && e.getAttribute('aria-hidden')!=='true' && e.getBoundingClientRect().height>0 && getComputedStyle(e).visibility!=='hidden' && getComputedStyle(e).display!=='none';
 const jump=document.querySelector('button[aria-label="Scroll to bottom"]');
 let panel=last;
 while(panel && !(panel.scrollHeight>panel.clientHeight+2 && /auto|scroll/.test(getComputedStyle(panel).overflowY)))panel=panel.parentElement;
 const atBottom=!!panel && panel.scrollTop+panel.clientHeight>=panel.scrollHeight-3;
 const busy=!!last?.querySelector('[aria-busy="true"]') || !!document.querySelector('button[aria-label="Stop response"]');
 return {chad_last_message:true,url:location.href,at_bottom:atBottom,
   jump_target:visible(jump)?'css=button[aria-label="Scroll to bottom"]':null,
   body_target:body?'css=model-response >> nth=-1 >> css=.markdown':null,
   completed:!!body && !busy && body.innerText.trim().length>0,
   char_count:body?Array.from(body.innerText).length:0,response_count:messages.length};
}"""


def source_url(contract):
    objective = contract['objective']
    if not re.search(r'last|latest|final', objective, re.I) or not re.search(r'capture|copy|verbatim', objective, re.I):
        return None
    urls = re.findall(r'https://gemini\.google\.com/app/[a-zA-Z0-9]+', objective)
    return urls[0] if len(set(urls)) == 1 else None


def next_capture_action(progress, tools):
    url = source_url(progress['contract'])
    if not url or progress['contract']['deliverable'] != 'content_id':
        return None
    required = {'browser_navigate', 'browser_click', 'browser_evaluate', 'browser_capture_text', 'browser_wait_for'}
    if not required <= set(tools):
        return None
    phase = progress.get('capture_phase', 'open')
    progress['milestone'] = 'Capture the last completed response'
    if progress.get('last_error'):
        progress['workflow_errors'] = progress.get('workflow_errors', 0) + 1
        phase = 'inspect'
        if progress['workflow_errors'] > 2:
            return {'action': 'browser_finish', 'arguments': {'status': 'incomplete', 'summary': 'Last-response capture stopped after tool errors; no text was verified.'}}
    def action(name, arguments, goal, next_phase):
        progress['capture_phase'] = next_phase
        return {'action': name, 'arguments': arguments, 'next_goal': goal}
    if phase == 'open':
        return action('browser_navigate', {'url': url}, 'Open the selected conversation', 'position')
    if phase == 'position':
        for line in progress['observation'].splitlines():
            if 'Scroll to bottom' in line:
                ref = re.search(r'(?:target="|\[ref=)((?:f\d+)?e\d+)', line)
                if ref:
                    return action('browser_click', {'target': ref[1]}, 'Jump to the last response', 'inspect')
        phase = 'inspect'
    if phase == 'read_inspection':
        text = result_text(progress['last_result'])
        marker = text.find('### Result')
        candidate = text[marker + len('### Result'):].lstrip() if marker >= 0 else text.lstrip()
        try:
            info, _ = json.JSONDecoder().raw_decode(candidate)
        except ValueError:
            info = {}
        if info.get('chad_last_message') and info.get('url') == url:
            if info.get('at_bottom') and info.get('completed') and info.get('body_target'):
                progress['capture_phase'] = 'capturing'
                return action('browser_capture_text', {'target': info['body_target']}, 'Save the exact last response', 'capturing')
            if info.get('jump_target'):
                # Selector comes from our fixed DOM inspection, never model prose.
                return action('browser_click', {'target': info['jump_target']}, 'Jump to the last response', 'inspect')
        progress['capture_waits'] = progress.get('capture_waits', 0) + 1
        if progress['capture_waits'] > 3:
            return {'action': 'browser_finish', 'arguments': {'status': 'incomplete', 'summary': 'The final completed response could not be verified at the conversation bottom.'}}
        return action('browser_wait_for', {'time': 1}, 'Wait for the last response to finish loading', 'inspect')
    return action('browser_evaluate', {'function': INSPECT_LAST_MESSAGE}, 'Verify the last completed response at the bottom', 'read_inspection')
