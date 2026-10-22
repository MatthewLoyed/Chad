"""TTFT before/after probe for the prefix-stable prompt change.

Measures the real time-to-first-token impact of moving per-turn-volatile content
(RAG boost, plan status) OUT of the system prompt and into the trailing user turn.

It replays the same realistic multi-turn conversation against the live LM Studio
model two ways:

  BEFORE (old assembly): system prompt = [stable persona] + [volatile RAG for this
      turn]. Because the RAG block changes every turn, the system prefix changes,
      so the local model must re-prefill the whole conversation each turn.

  AFTER (new assembly): system prompt = [stable persona] only. The volatile RAG for
      the turn rides in the trailing user message. The system prompt + prior history
      stay a byte-stable prefix, so the KV cache is reused.

Turn 1 is cold in both cases; the win shows up on turns 2+. Uses LM Studio's native
/api/v0 endpoint, which reports time_to_first_token directly.

Run:  .venv/Scripts/python scripts/ttft_cache_probe.py
"""
import json
import time
import urllib.request

BASE = "http://localhost:1234"
URL = BASE + "/api/v0/chat/completions"


def _loaded_model() -> str:
    with urllib.request.urlopen(BASE + "/api/v0/models", timeout=10) as r:
        data = json.loads(r.read())
    for m in data.get("data", []):
        if m.get("state") == "loaded":
            return m["id"]
    raise RuntimeError("No model is loaded in LM Studio.")


# --- Realistic prompt material -------------------------------------------------
# ~stable persona block (stands in for Chad's Supabase persona + static orchestration).
PERSONA = (
    "You are Chad, a local-first personal AI assistant. Be direct and helpful. "
    "You orchestrate tools and specialist sub-agents. Prefer to act in parallel when "
    "actions are independent. Always report failures honestly. " * 120
).strip()

# Per-turn RAG blocks — DIFFERENT every turn (query-dependent), like the real
# graph + activity boost. This is the content that (in the old design) mutated the
# system prefix each turn.
RAG_BLOCKS = [
    "\n\n### RELATIONAL CONTEXT\n- The user is planning a wedding in May 2027.\n"
    "- The user works at Redstone Arsenal.\n",
    "\n\n### ACTIVITY CONTEXT\n- Recently browsed: llama.cpp prompt caching docs.\n"
    "- Opened: KV cache reuse GitHub discussion.\n",
    "\n\n### RELATIONAL CONTEXT\n- The user's desktop runs a high-VRAM GPU.\n"
    "- Prefers small local models (Qwen, Gemma) over cloud.\n",
]

USER_TURNS = [
    "What's the fastest way to speed up my assistant's first-token latency?",
    "Okay, and how does that interact with running several agents at once?",
    "Got it. Summarize the plan in two sentences.",
]


def call(model: str, messages: list, tag: str) -> float:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 12,
        "stream": False,
    }
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=180) as r:
        body = json.loads(r.read())
    wall = time.perf_counter() - t0
    stats = body.get("stats", {})
    ttft = stats.get("time_to_first_token")
    ptoks = body.get("usage", {}).get("prompt_tokens")
    print(f"  [{tag}] TTFT={ttft:.3f}s  wall={wall:.2f}s  prompt_tokens={ptoks}")
    return float(ttft) if ttft is not None else wall


def run_strategy(model: str, stable_system: bool) -> list:
    """Replay the conversation; return per-turn TTFT list. Fresh history each run."""
    history: list = []
    ttfts: list = []
    for i, user_turn in enumerate(USER_TURNS):
        rag = RAG_BLOCKS[i % len(RAG_BLOCKS)]
        if stable_system:
            system = PERSONA  # byte-stable across turns
            user_content = f"{user_turn}\n\n---\n[Turn context:]{rag}"
        else:
            system = PERSONA + rag  # volatile prefix — changes every turn
            user_content = user_turn

        messages = [{"role": "system", "content": system}] + history + [
            {"role": "user", "content": user_content}
        ]
        tag = f"turn {i + 1} ({'AFTER ' if stable_system else 'BEFORE'})"
        ttfts.append(call(model, messages, tag))

        # Grow the conversation (append-only), like a real multi-turn session.
        history.append({"role": "user", "content": user_content})
        history.append({"role": "assistant", "content": "(short answer)"})
    return ttfts


def main() -> None:
    model = _loaded_model()
    print(f"Model under test: {model}\n")

    # Prime once so both strategies start from the same (persona-cached) baseline,
    # isolating the variable we care about: per-turn prefix stability.
    print("Priming persona prefix...")
    call(model, [{"role": "system", "content": PERSONA}, {"role": "user", "content": "hi"}], "prime")

    print("\n=== BEFORE: volatile RAG in the system prompt (old assembly) ===")
    before = run_strategy(model, stable_system=False)

    print("\n=== AFTER: stable system prompt, RAG in the trailing user turn (new assembly) ===")
    after = run_strategy(model, stable_system=True)

    print("\n=== RESULT (warm turns 2+, where caching matters) ===")
    b_warm = sum(before[1:]) / len(before[1:])
    a_warm = sum(after[1:]) / len(after[1:])
    print(f"  BEFORE avg TTFT (turns 2-3): {b_warm:.3f}s")
    print(f"  AFTER  avg TTFT (turns 2-3): {a_warm:.3f}s")
    if a_warm > 0:
        print(f"  Speedup: {b_warm / a_warm:.1f}x faster to first token")
    print(f"  Per-turn BEFORE: {[round(x, 3) for x in before]}")
    print(f"  Per-turn AFTER : {[round(x, 3) for x in after]}")


if __name__ == "__main__":
    main()
