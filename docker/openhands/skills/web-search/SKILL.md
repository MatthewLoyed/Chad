---
name: web-search
description: Look things up on the web when you're stuck or need current info — library docs (Context7), and general web search (Brave / local SearXNG) via the terminal. Use sparingly, only when your own knowledge is insufficient or possibly out of date.
---

# Web Search & Docs

You are offline-by-default: rely on the code and your own knowledge first. But when you hit a
real unknown — an unfamiliar library API, a confusing error, something that may have changed —
you can look it up. Two channels:

## 1. Library / API documentation → Context7 tools (preferred for docs)
If Context7 tools are in your toolset (`resolve-library-id`, `query-docs`), use them for anything
about a specific library, framework, or SDK — they return **current, version-accurate** docs,
which is far more reliable than guessing an API from memory.
1. `resolve-library-id` with the library name (e.g. "fastapi", "react-router") → a library id.
2. `query-docs` with that id + your question (e.g. "how to add a middleware").

Reach for this the moment you're unsure of an import path, a function signature, a config option,
or why a library call errors.

## 2. General web search → terminal + curl (Brave)
For up-to-date facts, error messages, or "what's the current way to X", use **Brave** via the
terminal (if `BRAVE_API_KEY` is set):
```bash
curl -s -H "X-Subscription-Token: $BRAVE_API_KEY" \
  "https://api.search.brave.com/res/v1/web/search?q=YOUR+QUERY&count=5" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(r['title'],'—',r['url'],'\n ',r.get('description','')) for r in d.get('web',{}).get('results',[])[:5]]"
```
Then `curl` a specific result URL if you need the page contents. Brave returns high-quality
results, so a couple of targeted queries usually suffices — no need to over-search.

## When NOT to search
- ❌ For things you already know or can read in the workspace/`/repo`.
- ❌ To avoid actually trying the code — run it and read the error first.
- ❌ Dozens of searches. A couple of targeted lookups, then get back to building.

## Reporting
If a search or docs lookup changed your approach, say so briefly in your final message (what you
looked up and what you learned), so the result is traceable.
