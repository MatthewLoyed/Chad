"""Explicit, bounded chat smoke test from the Brain's network perspective."""
import asyncio
from urllib.parse import urlsplit

from src.core.tools.failure import failure, BAD_INPUT, UNAVAILABLE, AUTH
from src.core.tools.limits import get as limit


async def test_model(base_url, api_key, model_id, confirmed=False):
    if confirmed is not True:
        return failure("Confirm the short chat test first. A hosted provider may charge for it.", failure_class=BAD_INPUT)
    base_url, model_id = str(base_url or "").strip(), str(model_id or "").strip()
    try:
        endpoint = urlsplit(base_url)
    except ValueError:
        return failure("Use a valid model API base address.", failure_class=BAD_INPUT)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        return failure("Use an HTTP or HTTPS model API address without credentials in its URL.", failure_class=BAD_INPUT)
    if not str(model_id or "").strip():
        return failure("Choose or enter a chat model ID before testing.", failure_class=BAD_INPUT)
    from src.core.intelligence.local_model_discovery import NON_CHAT_MODEL_TERMS
    if any(term in model_id.lower() for term in NON_CHAT_MODEL_TERMS):
        return failure("Choose a chat model, rather than an embedding, speech or reranking model.", failure_class=BAD_INPUT)
    from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
    adapter = LiteLLMAdapter(model_name=model_id, base_url=base_url.rstrip("/"), api_key=api_key or None)
    try:
        reply = await asyncio.wait_for(adapter.generate(
            [{"role": "user", "content": "Say hello in one short sentence."}],
            tools=None, max_tokens=int(limit("setup_test_tokens")), thinking_enabled=False,
        ), timeout=limit("setup_test_timeout"))
        if reply.finish_reason == "error" or not reply.text or not reply.text.strip() or reply.tool_calls:
            return failure("The model did not return a usable chat reply. Check its load state, API format and model ID, then retry.", failure_class=UNAVAILABLE)
        return {"success": True, "reply": reply.text[:1000], "tools_verified": False,
                "note": "Chat answered. Tool calling, images and reasoning compatibility have not been verified."}
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status in {401, 403}:
            return failure("The provider rejected access. Check the API key and model permissions.", failure_class=AUTH)
        if status == 429:
            return failure("The provider reported a rate or credit limit. Check your account before retrying.", failure_class=UNAVAILABLE)
        # Provider exceptions can contain credentials, URLs and request bodies.
        return failure("The chat test could not finish. Check the server, model load state, account credit and API format, then retry.", failure_class=UNAVAILABLE)
