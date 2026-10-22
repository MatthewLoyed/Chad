import asyncio
import litellm

litellm.set_verbose = False

async def test():
    response = await litellm.acompletion(
        model="openai/qwen3-4b",  # adjust if your model ID differs
        messages=[{"role": "user", "content": "What is 2+2? Think step by step."}],
        api_base="http://localhost:1234/v1",
        api_key="lm-studio",
        stream=True,
        temperature=0.6
    )
    async for chunk in response:
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        reasoning = getattr(delta, "reasoning_content", "MISSING")
        if content or reasoning != "MISSING":
            print(f"content={repr(content)} reasoning={repr(reasoning)}")

asyncio.run(test())
