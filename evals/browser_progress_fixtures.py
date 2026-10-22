"""Fictional pages for repeatable browser-controller evaluations."""
import html

SERMON = 'Dating sermon — patience and kindness.\n\nChoose honesty.\nKeep your promises. 🙏'
CASES = ('short', 'chat', 'nested', 'lazy', 'modal', 'stale', 'missing', 'distraction')


def task(case):
    url = f'http://fixture.test/{case}'
    if case == 'short':
        return f'Open {url} and report the verification code shown on the page. Do not browse further.'
    if case == 'missing':
        return f'Open {url}, find the finished dating sermon, and capture only its last complete response. If the conversation is absent, report blocked; never invent text.'
    return f'Open {url}, locate the Dating sermon conversation and capture only its last completed assistant response verbatim using browser_capture_text. Return its content_id. Do not export the chat or rewrite the response.'


def page_html(case):
    if case == 'short':
        return '<main><h1>Verification code</h1><p>CHAD-4729</p></main>'
    if case == 'missing':
        return '<main><h1>Conversations</h1><p>No matching conversations. The dating sermon is not available in this account.</p></main>'
    panel = 'height:180px;overflow-y:auto' if case in ('nested', 'lazy') else ''
    spacer = '<p style="height:650px">Earlier discussion, not the final sermon.</p>' if panel else '<p>Earlier discussion, not the final sermon.</p>'
    last = '<article data-key="sermon" style="white-space:pre-wrap">' + html.escape(SERMON) + '</article>'
    content = spacer + ('' if case == 'lazy' else last) + '<button data-key="copy" onclick="this.dataset.clicked=\'true\'">Copy response</button>'
    lazy = f"panel.addEventListener('scroll',()=>{{if(!document.querySelector('article'))panel.insertAdjacentHTML('beforeend',{last!r});}});" if case == 'lazy' else ''
    modal = '<dialog open id="overlay"><p>Privacy notice</p><button data-key="dismiss" onclick="this.parentElement.remove()">Dismiss notice</button></dialog>' if case == 'modal' else ''
    # Use a real overlay, not an arbitrary simulator error.
    modal_style = 'dialog{position:fixed;inset:0;width:90%;height:90%;z-index:5;background:white}'
    return f'''<html><head><style>{modal_style}</style></head><body>
    <main><h1>Conversations</h1>
    <button data-key="open" onclick="document.querySelector('#chat').hidden=false">Dating sermon</button>
    <button data-key="settings" onclick="this.dataset.visits=Number(this.dataset.visits||0)+1">Settings</button>
    <button data-key="archive" onclick="this.dataset.visits=Number(this.dataset.visits||0)+1">Archive settings</button>
    <section id="chat" hidden><h2>Dating sermon</h2><div id="panel" data-key="panel" style="{panel}">{content}</div></section>
    </main>{modal}
    <script>const panel=document.querySelector('#panel');{lazy}</script></body></html>'''


SNAPSHOT_JS = """() => {
  function visible(el) {
    if (el.closest('[hidden]')) return false;
    let r=el.getBoundingClientRect();
    if (!r.width||!r.height||r.bottom<0||r.top>innerHeight) return false;
    for(let p=el.parentElement;p;p=p.parentElement){
      if(/auto|scroll/.test(getComputedStyle(p).overflowY)){
        const a=p.getBoundingClientRect();if(r.bottom<=a.top||r.top>=a.bottom)return false;
      }
    } return true;
  }
  return [...document.querySelectorAll('h1,h2,p,button,input,[data-key="panel"],article')]
    .filter(visible).map((el,index)=>{if(!el.dataset.key)el.dataset.key='fixture-node-'+index;return {key:el.dataset.key,tag:el.tagName.toLowerCase(),
      text:el.tagName==='DIV'?'Scrollable conversation panel':el.innerText,
      scroll:el.scrollHeight>el.clientHeight};});
}"""
