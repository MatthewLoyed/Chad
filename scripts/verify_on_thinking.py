import asyncio
import sys
sys.path.insert(0, ".")

from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter

async def test():
    thinking_chunks = []
    content_chunks = []

    async def on_thinking(text, type="thinking", append=True, agent_name=None):
        thinking_chunks.append(text)
        print(f"[ON_THINKING] type={type} append={append} text={repr(text[:40])}")

    async def on_token(text):
        content_chunks.append(text)

    adapter = LiteLLMAdapter(
        model_name="openai/qwen3-4b",  # adjust if needed
        base_url="http://localhost:1234/v1",
        api_key="lm-studio"
    )

    result = await adapter.generate(
        messages=[{"role": "user", "content": "What is 2+2? Think step by step."}],
        on_thinking=on_thinking,
        on_token=on_token,
        temperature=0.6
    )

    print(f"\n--- SUMMARY ---")
    print(f"Thinking chunks received: {len(thinking_chunks)}")
    print(f"Content chunks received: {len(content_chunks)}")
    print(f"First thinking: {repr(''.join(thinking_chunks)[:100])}")
    print(f"First content: {repr(''.join(content_chunks)[:100])}")

asyncio.run(test())
