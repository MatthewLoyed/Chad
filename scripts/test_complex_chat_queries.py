"""
Test complex Chat Mode queries with real model in LM Studio.
Compares:
  Mode A: thinking_level = "off"
  Mode B: thinking_level = "minimal" (reasoning on)

Queries:
  1. "whats my mom and fiances email"
  2. "where do i live"
  3. "what is that one yt vid about bakugo i watched recently"
  4. "update me on my finances"
"""

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
from src.core.intelligence.graph_rag_client import GraphRAGClient
from config.model_registry import get_profile

# Sample read tools schemas available in Chat Mode
CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_contacts",
            "description": "Search personal contacts by name or relationship for phone numbers and email addresses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Name or relation (e.g. 'Mom', 'Noel', 'fiance')"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_browser_history",
            "description": "Search the user's Chrome/browser and YouTube watch history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query keywords (e.g. 'bakugo youtube')"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_financial_summary",
            "description": "Get overarching financial health snapshot including balances, net worth, and recent activity.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_transactions",
            "description": "Get recent bank and credit card transactions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of transactions"}
                },
            },
        },
    },
]

PROMPTS = [
    ("whats my mom and fiances email", "Contact / Graph query"),
    ("where do i live", "Core Profile location"),
    ("what is that one yt vid about bakugo i watched recently", "Browser / YouTube history"),
    ("update me on my finances", "Financial balances & snapshot"),
]


async def run_comparison():
    print("=" * 72)
    print("  COMPLEX CHAT QUERIES: THINKING 'OFF' vs 'MINIMAL'")
    print("=" * 72)

    base_url = "http://localhost:1234/v1"
    model_name = "prism-ml/bonsai-27b"
    adapter = LiteLLMAdapter(model_name=model_name, base_url=base_url, profile=get_profile(model_name), supports_tools=True)

    # Core profile / persona context
    core_profile = GraphRAGClient.read_core_profile_sync() or ""
    system_prompt = (
        "You are Chad in Fast Chat Mode. Answer questions directly and concisely.\n"
        "If you have the answer in your knowledge below, answer immediately.\n"
        "If you need external data (like contacts, YouTube history, or live finances), use the available tools.\n\n"
        f"KNOWLEDGE:\n{core_profile}"
    )

    for prompt, category_label in PROMPTS:
        print(f"\n{'='*72}")
        print(f"QUERY: '{prompt}' [{category_label}]")
        print(f"{'='*72}")

        for thinking_mode in ["off", "minimal"]:
            t0 = time.perf_counter()
            t_first_token = None
            thoughts = []
            tokens = []

            async def on_token(t):
                nonlocal t_first_token
                if t_first_token is None:
                    t_first_token = time.perf_counter()
                tokens.append(t)

            async def on_thinking(th, *args, **kwargs):
                thoughts.append(th)

            is_off = (thinking_mode == "off")
            res = await adapter.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                tools=CHAT_TOOLS,
                thinking_level=thinking_mode,
                thinking_enabled=(not is_off),
                temperature=0.0,
                on_token=on_token,
                on_thinking=on_thinking,
            )
            t1 = time.perf_counter()
            total_s = t1 - t0
            ttft_s = (t_first_token - t0) if t_first_token else total_s

            tool_calls = res.tool_calls or []
            tool_desc = ", ".join([f"{tc.get('name')}({tc.get('args')})" for tc in tool_calls]) if tool_calls else "Direct response (No tools called)"

            print(f"\n  [Thinking: {thinking_mode.upper():<7}] Latency: {total_s:.2f}s | TTFT: {ttft_s:.2f}s | Thoughts: {len(thoughts)}")
            print(f"    Action: {tool_desc}")
            if res.text:
                preview = res.text.strip().replace("\n", " ")[:120]
                print(f"    Text:   {preview}...")


if __name__ == "__main__":
    asyncio.run(run_comparison())
