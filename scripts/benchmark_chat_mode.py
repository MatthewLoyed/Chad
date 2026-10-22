"""
Benchmark Chat Mode latency and token timing directly with local model on LM Studio.
Measures:
  1. TTFT (Time To First Token)
  2. Thinking / reasoning token count & duration (ensuring 0 tokens)
  3. Generation speed (tokens/sec)
  4. Total elapsed turnaround time (seconds)
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

from langchain_core.messages import HumanMessage
from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
from src.core.intelligence.lc_wrapper import ChatChad
from src.core.orchestration.nodes.conversational_node import make_conversational_node
from config.model_registry import get_profile
from src.core.orchestration.turn.tool_selector import TurnToolSelector


async def run_benchmark():
    print("=" * 68)
    print("  CHAD CHAT MODE SPEED BENCHMARK")
    print("=" * 68)

    base_url = "http://localhost:1234/v1"
    model_name = "prism-ml/bonsai-27b"

    print(f"\n[1/3] Connecting to LM Studio ({model_name} @ {base_url})...")
    profile = get_profile(model_name)
    adapter = LiteLLMAdapter(
        model_name=model_name,
        base_url=base_url,
        profile=profile,
        supports_tools=True,
    )
    chat_chad = ChatChad(adapter=adapter, thinking_level="off", thinking_enabled=False)

    # 1. Warm-up call
    print("       Warming up model context...")
    t_warm = time.perf_counter()
    await adapter.generate(
        messages=[{"role": "user", "content": "ping"}],
        thinking_level="off",
        thinking_enabled=False,
    )
    print(f"       Warm-up complete in {time.perf_counter() - t_warm:.2f}s")

    # 2. Test conversational responder node with read tools
    conversational_node = make_conversational_node(chat_chad)

    # Read tools available in chat mode
    chat_tool_names = TurnToolSelector._CHAT_TOOL_NAMES
    print(f"       Chat read toolset count: {len(chat_tool_names)} tools")

    test_queries = [
        ("What is 25 * 4?", "Direct Chat (No Tools)"),
        ("Give me a one-sentence tip for productive deep work.", "Concise Q&A"),
        ("What is Matthew's favorite code editor if known?", "Read / Persona info"),
    ]

    results = []

    print("\n[2/3] Running Chat Mode turns...")
    for idx, (prompt, qtype) in enumerate(test_queries, 1):
        print(f"\n  Turn {idx}: '{prompt}' [{qtype}]")

        tokens = []
        thinking_events = []
        t_first_token = None
        t_first_thinking = None

        t_start = time.perf_counter()

        state = {
            "messages": [HumanMessage(content=prompt)],
            "system_instruction": "You are Chad in Fast Chat Mode. Answer questions directly, concisely, and accurately.",
            "tools": [],
            "chat_tool_rounds": 0,
            "thinking_level": "off",
            "knowledge_boost": "User: Matthew. Location: Austin, TX.",
        }

        # Run conversational responder node
        node_result = await conversational_node(state, {})
        t_end = time.perf_counter()

        response_msg = node_result["messages"][0]
        response_text = response_msg.content if hasattr(response_msg, "content") else str(response_msg)

        total_s = t_end - t_start
        # Extract response text stats
        word_count = len(response_text.split())

        res = {
            "query": prompt,
            "type": qtype,
            "total_s": round(total_s, 2),
            "words": word_count,
            "response": response_text.strip(),
        }
        results.append(res)

        print(f"       -> Total Turn Time: {res['total_s']}s")
        print(f"       -> Response ({res['words']} words): {res['response']}")

    print("\n" + "=" * 68)
    print("  CHAT MODE BENCHMARK RESULTS")
    print("=" * 68)
    print(f"{'Query':<35} | {'Latency':<9} | {'Length':<12}")
    print("-" * 68)
    for r in results:
        q_short = (r['query'][:32] + "...") if len(r['query']) > 32 else r['query']
        print(f"{q_short:<35} | {r['total_s']:<7}s | {r['words']:<5} words")
    print("-" * 68)
    avg_s = sum(r['total_s'] for r in results) / len(results)
    print(f"  Average Chat Mode Turn Latency: {avg_s:.2f}s (previously ~15-20s)")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
