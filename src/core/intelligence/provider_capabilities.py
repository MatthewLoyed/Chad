"""
Provider capability flags — declarative gating for backend-specific adapter workarounds.

A pile of LM-Studio-specific fixes (Qwen3 <think>-trapped tool calls, the froggeric
`<tool_response>` tag format, the `lm-studio` API key placeholder) used to fire off
ad-hoc `self.provider == "LM Studio"` / URL-sniffing checks scattered across
openai_adapter.py and litellm_adapter.py. For an Ollama / llama.cpp / vLLM user those
checks are dead weight at best and silently wrong at worst.

This module is the single source of truth for those flags, keyed on the *resolved*
provider identity (`providers.resolve_provider_name`) — the same identity #450a's
lifecycle plumbing uses — rather than a second, independent URL heuristic. Every flag
defaults to False/off, so any provider not explicitly listed below (Ollama, vLLM,
llama.cpp all resolve to "ollama"/"static") never triggers an LM Studio-only hack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProviderCapabilities:
    # Qwen3 on this backend binds json_schema / tool-call JSON to the *reasoning*
    # channel, leaving content empty (lmstudio-bug-tracker #1773) — thinking must be
    # forced off whenever tools or response_format are attached.
    needs_qwen3_reasoning_workaround: bool = False
    # This backend's chat template expects tool results wrapped in the froggeric
    # Qwen3 fixed-template `<tool_response>...</tool_response>` tags rather than the
    # generic `[TOOL RESULT: name]` prefix used for everything else.
    needs_tool_role_transform: bool = False
    # Whether this backend reliably converts raw model output into the OpenAI
    # `tool_calls` response field on its own. False means the dirty-parser
    # (froggeric `<function=...>` / XML `<tool_call>`) fallback is expected to be
    # needed occasionally, not just as a theoretical safety net.
    has_native_tool_parser: bool = True
    # reasoning_effort must be mirrored into `extra_body` for this backend's endpoint
    # to actually read it, in addition to (or instead of) the top-level param.
    reasoning_effort_via_extra_body: bool = False
    # Placeholder API key to send when the caller supplied none (many local servers
    # don't check the key but reject requests with no Authorization header at all).
    default_api_key: Optional[str] = None
    # Substrings that, when found in a mid-request HTTP error body, mean "the model
    # got evicted out from under this request" (a TOCTOU race between a residency
    # check and the request landing) rather than a genuine request error — signals
    # the caller should force a fresh residency check and retry once instead of
    # aborting the turn. Empty by default: no known backend has a real sample
    # collected yet, so the retry simply never fires there (harmless — see #520).
    residency_loss_markers: tuple[str, ...] = ()


_CAPABILITIES: dict[str, ProviderCapabilities] = {
    # OpenAI's client requires a nonempty key even when a custom endpoint
    # deliberately has no authentication. This public sentinel is not a secret;
    # an explicitly configured key still takes precedence in the adapter.
    "static": ProviderCapabilities(default_api_key="not-required"),
    "vllm": ProviderCapabilities(default_api_key="not-required"),
    "llamacpp": ProviderCapabilities(default_api_key="not-required"),
    "lmstudio": ProviderCapabilities(
        needs_qwen3_reasoning_workaround=True,
        needs_tool_role_transform=True,
        has_native_tool_parser=False,
        reasoning_effort_via_extra_body=True,
        default_api_key="lm-studio",
        # LM Studio's HTTP 400 body for a mid-request eviction reads
        # "Model is unloaded." — see llm_selector.py's TOCTOU self-heal.
        residency_loss_markers=("unload",),
    ),
}

# Unknown providers and cloud requests keep the conservative defaults.
_DEFAULT_CAPABILITIES = ProviderCapabilities()


def get_provider_capabilities(provider: str) -> ProviderCapabilities:
    """Look up capability flags by resolved provider key (``lmstudio``/``ollama``/``static``)."""
    return _CAPABILITIES.get((provider or "").strip().lower(), _DEFAULT_CAPABILITIES)


def get_capabilities_for_url(base_url: Optional[str]) -> ProviderCapabilities:
    """Resolve a base_url to its provider identity, then look up capability flags."""
    if not base_url:
        return _DEFAULT_CAPABILITIES
    from src.core.intelligence.providers import resolve_provider_name

    return get_provider_capabilities(resolve_provider_name(base_url))


def is_residency_loss_error(capabilities: ProviderCapabilities, err_body: str) -> bool:
    """Classify a mid-request HTTP error body as a residency-loss (model evicted out
    from under an in-flight request) per this backend's known error wording."""
    if not err_body or not capabilities.residency_loss_markers:
        return False
    err_lower = err_body.lower()
    return any(marker in err_lower for marker in capabilities.residency_loss_markers)
