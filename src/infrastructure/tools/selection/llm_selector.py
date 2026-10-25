"""
LLM-based tool selector using a small local model (e.g. Qwen 3.5 9B).

Replaces the RAG + core-injection pipeline with a single LLM call that picks
exactly the tools needed for a query from the full catalog. Reduces tool bloat
fed to the main model by ~90% on typical queries.

Backend-agnostic: the selector talks OpenAI-compatible ``/chat/completions`` and
routes all model-residency decisions (which model is loaded, load/unload, TOCTOU
retries) through the shared provider abstraction (``providers`` + ``model_lifecycle``),
so it works against LM Studio, Ollama, or a llama.cpp/vLLM static endpoint without
any backend-specific branching of its own. Provider-specific quirks (auth-key
placeholder, resident-model listing semantics) come from ``provider_capabilities``
and each backend's :class:`ProviderLifecycle`, never from URL sniffing here.

Falls back to None (caller should use RAG) if the backend is unreachable or returns
malformed output.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Callable

from src.core.intelligence.model_lifecycle import (
    ensure_resident_sync, release_sync, current_priority, active_use_sync,
    list_loaded_sync, OWNER_TOOL_SELECTOR,
)
from src.core.intelligence.provider_capabilities import get_capabilities_for_url, is_residency_loss_error
from src.shared.logger import setup_logger

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        return lambda func: func

logger = setup_logger("LLMToolSelector")

SYSTEM_PROMPT = """\
You are a tool selector for Chad, an AI assistant.

Given a user query, what Chad already has available, and a catalog of candidate tools, \
return a JSON array of ONLY additional tools needed to perform explicit user requests.

What Chad already has:
- Core tools permanently bound (listed below)
- Specialist subagents (browser agent autonomously handles all web browsing, store shopping, and cart actions; do NOT attempt to select browser or delegation tools)
- Personal memory and preferences loaded in context (do NOT pick tools to look up known user facts or preferences)

Rules:
- Return ONLY a raw JSON array of strings, e.g. ["tool_a", "tool_b"] or []
- No explanation, no markdown fences, no extra text — raw JSON only
- Select tools ONLY for explicit actions requested (e.g. 'send an email' -> send_email, 'create a document' -> google_drive_create_doc)
- Do NOT select tools to look up user preferences or facts already in personal context (e.g. do NOT pick search_browser_history to find favorite shows, hobbies, or info already known)
- Do NOT pick media tools (like show_youtube_video or play_youtube_music) unless the user explicitly requested to play or watch video/music
- Do NOT include any permanently bound core tools or specialist agents
- If all actions are covered by Chad's existing context, core tools, and specialist agents, return []
- Tool names must match exactly as listed in the candidate tools"""

# LM Studio blocks on /api/v1/models/load until the model is ready.
_DEFAULT_LOAD_TIMEOUT = 180.0
_DEFAULT_SELECT_TIMEOUT = 90.0
# Large models used as a fallback selector (e.g. 35B when 9B isn't loaded) need
# more time — full-catalog selection can exceed 45s under GPU load.
_LARGE_MODEL_SELECT_TIMEOUT = 120.0


def shared_setup_selector(registry):
    """Refresh a pre-existing registry after first-run setup without a restart."""
    from src.shared.config_manager import ConfigManager

    current = registry._llm_selector
    if ConfigManager.get("setup.share_main_model", False) or ConfigManager.get("tools.selector_model"):
        from src.shared.model_selection import selector_config
        selected = selector_config()
        if (getattr(current, "_model_id", None) != ConfigManager.get_llm_selector_model()
                or getattr(current, "_base_url", "").rstrip("/") != selected.get("base_url", "").rstrip("/")):
            from src.infrastructure.tools.registry import _build_llm_selector

            registry._llm_selector = current = _build_llm_selector()
    return current


def _selector_request_headers(base_url, capabilities):
    from src.shared.config_manager import ConfigManager

    from src.shared.model_selection import selector_config
    selected = selector_config()
    api_key = capabilities.default_api_key
    if selected.get("base_url", "").rstrip("/") == base_url.rstrip("/"):
        api_key = selected.get("api_key") or api_key
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


class LLMToolSelector:
    """
    Calls a small local LLM to select the exact tools needed for a query.
    Instantiate once and reuse; availability is checked lazily.
    """

    def __init__(self, model_id: str, base_url: str = "http://localhost:1234/v1",
                 strict_model: bool = False, unload_after_select: bool = False,
                 context_length: int = 32768):
        self._model_id = model_id
        self._strict_model = strict_model
        self._unload_after_select = unload_after_select
        # LM Studio JIT-loads a model with whatever context length was last saved for it in
        # the UI (often a small default, e.g. 4096) unless a request specifies one. The
        # full-catalog fallback (when the hybrid RAG shortlist is empty) plus 2-turn history
        # can exceed a small default and gets rejected with HTTP 400 — which fail-fast mode
        # then turns into a full turn abort. Request a context generous enough for the
        # largest catalog + short history so the selector call itself never overflows.
        self._context_length = context_length
        self._base_url = base_url.rstrip("/")
        self._completions_url = self._base_url + "/chat/completions"
        self._models_url = self._base_url + "/models"
        # Provider capability flags (auth-key placeholder, etc.) resolved from the
        # backend identity — not a URL heuristic — so this selector shares the single
        # source of truth every adapter/lifecycle path uses. Some static endpoints
        # reject requests with no Authorization header at all; default_api_key supplies
        # the placeholder those backends expect (None for LM Studio, which ignores it).
        self._capabilities = get_capabilities_for_url(self._base_url)
        self._resolved_model_id: Optional[str] = None
        self._server_reachable: Optional[bool] = None
        self._unavailable_reason: Optional[str] = None
        self._last_select_meta: Dict[str, Any] = {}
        self._main_model_hint: Optional[str] = None

    def set_main_model_hint(self, model_id: Optional[str]) -> None:
        """Tell the selector which model is the main chat model for this turn.

        When the selector model and the main model are the same, unload is skipped —
        we must not evict the model that is about to generate the reply.
        """
        self._main_model_hint = model_id

    @property
    def last_unavailable_reason(self) -> Optional[str]:
        """Human-readable reason the selector last reported unavailable."""
        return self._unavailable_reason

    @property
    def last_select_meta(self) -> Dict[str, Any]:
        """Metadata from the most recent select() call (for traces/logs)."""
        return dict(self._last_select_meta)

    def get_diagnostics(self) -> Dict[str, Any]:
        """Snapshot of selector health — safe to log or attach to traces."""
        loaded = self._list_loaded_models() if self._is_server_reachable() else []
        return {
            "configured_model": self._model_id,
            "resolved_model": self._resolved_model_id,
            "base_url": self._base_url,
            "server_reachable": self._server_reachable,
            "loaded_models": loaded,
            "available": self.is_available(),
            "unavailable_reason": self._unavailable_reason,
            "last_select": self._last_select_meta,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def warmup(self) -> bool:
        """Pre-load the selector model so the first turn does not pay load latency."""
        ok = self._ensure_model_loaded()
        try:
            from src.shared.startup_timing import set_flag
            if ok:
                set_flag("llm_tool_selector_lmstudio", f"reachable, model={self._resolved_model_id}")
            else:
                set_flag("llm_tool_selector_lmstudio", f"UNREACHABLE ({self._unavailable_reason})")
        except Exception:
            pass
        return ok

    def is_available(self) -> bool:
        """Return True when the inference backend is reachable and a selector model is loaded."""
        ok = self._ensure_model_loaded()
        if not ok and not self._unavailable_reason:
            self._unavailable_reason = "unknown_availability_failure"
        return ok

    @traceable(run_type="llm", name="LLMToolSelector")
    def select(self, query: str, tools: Dict[str, Any],
               history: Optional[List[Any]] = None,
               on_completions_start: Optional[Callable[[], None]] = None,
               knowledge_context: Optional[str] = None,
               core_tools: Optional[List[str]] = None) -> Optional[List[str]]:
        """
        Ask the LLM to pick tool names from the catalog.

        `history` (recent dialogue turns) is folded into the prompt so the selector can
        resolve coreferences like "turn it off" — without it, the selector only ever sees
        the bare current-turn query and has no way to know what "it" refers to.

        `knowledge_context` is the same personal knowledge boost the supervisor receives
        (graph / activity / drive / session). It lets the selector infer e.g. Gmail tools
        when the query never says "email" but activity hits are email threads.

        `core_tools` are tools already permanently active on the assistant. The selector
        is informed of them so it does not waste picks choosing them.

        Returns a list of valid tool name strings, or None if the call failed
        (caller should fall back to RAG).
        """
        knowledge_snippet = (knowledge_context or "").strip()
        self._last_select_meta = {
            "query": query,
            "catalog_size": len(tools),
            "had_knowledge_context": bool(knowledge_snippet),
        }

        if not self._ensure_model_loaded():
            self._last_select_meta.update({
                "status": "skipped",
                "reason": self._unavailable_reason or "model_not_loaded",
            })
            return None

        catalog = self._build_catalog(tools)
        if not catalog:
            self._unavailable_reason = "empty_tool_catalog"
            self._last_select_meta.update({"status": "failed", "reason": self._unavailable_reason})
            return None

        catalog_text = "\n".join(f'  "{n}": "{d}"' for n, d in catalog.items())
        # Keep the window small (2 turns) — this is only for coreference grounding, not
        # retrieval recall, so it doesn't need the 5-turn window the RAG shortlist uses.
        from src.core.intelligence.query_synthesizer import QuerySynthesizer
        enriched_query = QuerySynthesizer.enrich_with_history(query, history, max_turns=2)
        parts: List[str] = []
        if enriched_query != query:
            parts.append(
                f'Recent conversation (for resolving "it"/"that"/pronouns only — '
                f'the request to fulfill is current_intent):\n{enriched_query}'
            )
        else:
            parts.append(f'User query: "{query}"')
        if knowledge_snippet:
            parts.append(
                "PERSONAL CONTEXT (Chad already has this in memory — user facts, preferences, recent activity):\n"
                f"{knowledge_snippet}\n"
                "Do NOT pick tools to look up facts/preferences already stated above."
            )
        if core_tools:
            core_list_str = ", ".join(sorted(core_tools))
            parts.append(
                f"Tools ALREADY available and permanently bound to Chad for this turn:\n"
                f"[{core_list_str}]\n"
                f"Specialist agents: [delegate_to_browser_agent, delegate_to_code_agent, delegate_to_research_agent, delegate_to_canvas_agent] (always bound; handle browser navigation, cart actions, coding, canvas)\n"
                f"Do NOT pick any of the above tools or specialist agents — they are already provided."
            )
        parts.append(f"Additional candidate tools to choose from (pick ONLY if relevant):\n{{\n{catalog_text}\n}}")
        user_msg = "\n\n".join(parts)

        def _build_request() -> urllib.request.Request:
            model = self._resolved_model_id or self._model_id
            self._last_select_meta["model"] = model
            payload: Dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.0,
                # Reasoning models (Qwen3.x) spend completion budget on reasoning_content before
                # ever reaching the answer — 512 was getting fully consumed by reasoning on harder
                # catalogs, truncating the response (finish_reason="length", empty content, parse
                # failure). Headroom here is cheap; the answer itself is a short JSON array.
                "max_tokens": 1536,
            }
            # Qwen 3.x traps JSON inside thinking blocks unless thinking is disabled.
            if "qwen3" in model.lower():
                payload["extra_body"] = {
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            return urllib.request.Request(
                self._completions_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=self._request_headers(),
                method="POST",
            )

        if on_completions_start:
            try:
                on_completions_start()
            except Exception:
                pass

        select_timeout = self._select_timeout()
        t0 = time.perf_counter()
        try:
            # A backend can report a model as loaded (via /v1/models) and then evict it
            # (idle TTL, VRAM pressure, another owner's eviction) in the gap before this
            # request actually lands — a TOCTOU race no residency check can fully close.
            # Whether — and how — a given backend surfaces that mid-request is per-provider
            # (LM Studio: HTTP 400 "Model is unloaded."; see provider_capabilities's
            # residency_loss_markers). When it matches, treat it as a not-loaded signal:
            # force a fresh residency check (bypassing the loaded-models cache) and retry
            # the request once instead of aborting the whole turn.
            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                model = self._resolved_model_id or self._model_id
                req = _build_request()
                try:
                    # Mark the model actively in-use for the exact duration of the network
                    # call so the model-lifecycle coordinator (model_lifecycle.py) will never
                    # evict it out from under this in-flight request.
                    with active_use_sync(self._base_url, model):
                        with urllib.request.urlopen(req, timeout=select_timeout) as resp:
                            body = json.loads(resp.read())
                            raw = self._extract_message_content(body)
                            finish_reason = body["choices"][0].get("finish_reason")
                    break
                except urllib.error.HTTPError as e:
                    # LM Studio's error body has the actual reason (e.g. context overflow,
                    # bad request field) — the bare HTTPError str() is just "HTTP Error 400:
                    # Bad Request", which is useless for diagnosing fail-fast aborts.
                    try:
                        err_body = e.read().decode("utf-8", errors="replace")[:500]
                    except Exception:
                        err_body = "(could not read error body)"

                    if attempt < max_attempts and is_residency_loss_error(self._capabilities, err_body):
                        logger.warning(
                            f"[ LLM_SELECTOR ] '{model}' reported unloaded mid-request "
                            f"(attempt {attempt}/{max_attempts}) — forcing reload and retrying."
                        )
                        self._loaded_models_cache = None
                        self._loaded_models_cache_time = 0
                        self._resolved_model_id = None
                        if self._ensure_model_loaded():
                            continue

                    # A bare 5xx (LM Studio/gate returning "Internal Server Error", not a
                    # residency-loss signal) is usually a transient hiccup — mid-swap, gate
                    # momentarily overloaded — not a real "this backend is down" state. Retry
                    # once before giving up rather than immediately fail-fasting the turn on it.
                    if attempt < max_attempts and e.code and e.code >= 500:
                        logger.warning(
                            f"[ LLM_SELECTOR ] '{model}' returned HTTP {e.code} "
                            f"(attempt {attempt}/{max_attempts}) — retrying once."
                        )
                        continue

                    reason = f"http_error: {e} — {err_body}"
                    self._unavailable_reason = reason
                    self._last_select_meta.update({
                        "status": "failed",
                        "reason": reason,
                        "latency_s": round(time.perf_counter() - t0, 2),
                    })
                    logger.warning(
                        f"[ LLM_SELECTOR ] Request failed after {select_timeout:.0f}s "
                        f"(model={model}): {e} — {err_body}"
                    )
                    return None
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    reason = f"http_error: {e}"
                    self._unavailable_reason = reason
                    self._last_select_meta.update({
                        "status": "failed",
                        "reason": reason,
                        "latency_s": round(time.perf_counter() - t0, 2),
                    })
                    logger.warning(
                        f"[ LLM_SELECTOR ] Request failed after {select_timeout:.0f}s "
                        f"(model={model}): {e}"
                    )
                    return None
                except (KeyError, json.JSONDecodeError, IndexError) as e:
                    reason = f"malformed_response: {e}"
                    self._unavailable_reason = reason
                    self._last_select_meta.update({
                        "status": "failed",
                        "reason": reason,
                        "latency_s": round(time.perf_counter() - t0, 2),
                    })
                    logger.warning(f"[ LLM_SELECTOR ] Malformed response: {e}")
                    return None

            latency = time.perf_counter() - t0

            if finish_reason == "length":
                logger.warning(
                    f"[ LLM_SELECTOR ] Response hit max_tokens before answering "
                    f"(model={model}, catalog={len(tools)} tools) — reasoning likely "
                    f"consumed the whole budget. Raising max_tokens further may help."
                )

            selected = self._parse(raw, set(tools.keys()))
            if selected is None:
                reason = "parse_failed" if finish_reason != "length" else "truncated_by_max_tokens"
                self._unavailable_reason = reason
                self._last_select_meta.update({
                    "status": "failed",
                    "reason": reason,
                    "finish_reason": finish_reason,
                    "raw_snippet": raw[:200],
                    "latency_s": round(latency, 2),
                })
                logger.warning(
                    f"[ LLM_SELECTOR ] Could not parse selector output for '{query}'. "
                    f"finish_reason={finish_reason!r} Raw snippet: {raw[:200]!r}"
                )
                return None

            self._unavailable_reason = None
            self._last_select_meta.update({
                "status": "ok",
                "selected": selected,
                "finish_reason": finish_reason,
                "latency_s": round(latency, 2),
            })
            logger.info(
                f"[ LLM_SELECTOR ] '{query}' → {selected} ({latency:.1f}s)"
            )
            return selected
        finally:
            self._maybe_unload_after_use()

    # ------------------------------------------------------------------
    # LM Studio lifecycle
    # ------------------------------------------------------------------

    def _select_timeout(self) -> float:
        """Scale timeout when reusing a larger loaded model for selection."""
        model = (self._resolved_model_id or self._model_id).lower()
        if self._resolved_model_id and self._resolved_model_id != self._model_id:
            return _LARGE_MODEL_SELECT_TIMEOUT
        if any(tag in model for tag in ("35b", "32b", "27b", "70b", "72b")):
            return _LARGE_MODEL_SELECT_TIMEOUT
        return _DEFAULT_SELECT_TIMEOUT

    # Shared with model_lifecycle.warm_models's warm claim (refs #592) — a claim under any
    # other tag would never get released, permanently blocking this class's own unload.
    _OWNER = OWNER_TOOL_SELECTOR

    def _ensure_model_loaded(self) -> bool:
        """
        Verify the inference backend is up and a model is available for tool selection.

        Preference order:
        1. Configured selector model (exact or quant-suffix match)
        2. Load the selector model when the backend is idle (nothing loaded)
        3. Reuse whatever model is already loaded (avoids evicting the user's
           active chat model when a different weights file is in memory)

        The actual load/unload HTTP calls, locking, and VRAM-budget eviction are delegated to
        ``model_lifecycle`` (the shared coordinator) — this method only decides WHICH model to
        target and registers our claim on it so nothing else evicts it mid-selection.
        """
        if not self._is_server_reachable():
            self._unavailable_reason = "lm_studio_unreachable"
            return False

        # Read once here (same thread/task as the caller) — an autonomous/heartbeat turn's
        # claim queues behind an active interactive turn instead of evicting it out from
        # under a live conversation. See model_lifecycle.current_priority().
        priority = current_priority()

        loaded_ids = self._list_loaded_models()
        matched = self._match_model_id(self._model_id, loaded_ids)
        if matched:
            ensure_resident_sync(self._base_url, matched, owner=self._OWNER, priority=priority)
            self._resolved_model_id = matched
            self._unavailable_reason = None
            return True

        # Non-strict (default): reuse whatever is already loaded rather than evicting the user's
        # active chat model. This is convenient but NON-DETERMINISTIC — different turns can run on
        # different models, which shows up as tool-selection variance. Strict mode pins the
        # configured selector model: load it first, only reuse a different model if the load fails.
        if loaded_ids and not self._strict_model:
            self._resolved_model_id = loaded_ids[0]
            ensure_resident_sync(self._base_url, self._resolved_model_id, owner=self._OWNER, priority=priority)
            self._unavailable_reason = None
            logger.info(
                f"[ LLM_SELECTOR ] Selector model '{self._model_id}' not loaded; "
                f"reusing active LM Studio model '{self._resolved_model_id}'."
            )
            return True

        logger.info(
            f"[ LLM_SELECTOR ] Model '{self._model_id}' not loaded — "
            f"requesting load from LM Studio..."
        )
        if ensure_resident_sync(
            self._base_url,
            self._model_id,
            owner=self._OWNER,
            priority=priority,
            timeout=_DEFAULT_LOAD_TIMEOUT,
            context_length=self._context_length,
        ):
            self._resolved_model_id = self._model_id
            self._unavailable_reason = None
            logger.info(f"[ LLM_SELECTOR ] Model '{self._model_id}' loaded and ready.")
            return True

        # Last resort even in strict mode: reuse a loaded model rather than disabling the
        # selector entirely (which would force the RAG-only fallback or abort the turn).
        loaded_ids = self._list_loaded_models()
        if loaded_ids:
            self._resolved_model_id = loaded_ids[0]
            ensure_resident_sync(self._base_url, self._resolved_model_id, owner=self._OWNER, priority=priority)
            self._unavailable_reason = None
            logger.warning(
                f"[ LLM_SELECTOR ] Could not load '{self._model_id}'; "
                f"reusing loaded model '{self._resolved_model_id}' as last resort."
            )
            return True

        self._unavailable_reason = f"load_failed:{self._model_id}"
        logger.warning(f"[ LLM_SELECTOR ] Failed to load '{self._model_id}' in LM Studio.")
        return False

    def _request_headers(self) -> Dict[str, str]:
        """Use the selected endpoint's credential, otherwise its provider default."""
        return _selector_request_headers(self._base_url, self._capabilities)

    def _is_server_reachable(self) -> bool:
        if self._server_reachable is True:
            return True
        try:
            req = urllib.request.Request(
                self._models_url, headers=self._request_headers(), method="GET"
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status != 200:
                    self._server_reachable = False
                    return False
            self._server_reachable = True
            return True
        except Exception as e:
            self._server_reachable = False
            self._unavailable_reason = f"backend_unreachable: {e}"
            logger.warning(f"[ LLM_SELECTOR ] Inference backend unreachable: {e}")
            return False

    def _list_loaded_models(self) -> List[str]:
        """Resident model ids on this backend, via the shared provider abstraction.

        Delegates to ``model_lifecycle.list_loaded_sync`` (which resolves the backend's
        :class:`ProviderLifecycle`) rather than GETting ``/v1/models`` directly — that raw
        listing reports *pulled* rather than *resident* models on Ollama, which would let the
        reuse/eviction logic act on a model that isn't actually in VRAM. Cached for 5s to
        avoid hammering the backend across the reachability/select/unload calls in one turn.
        """
        now = time.monotonic()
        if (
            hasattr(self, "_loaded_models_cache")
            and getattr(self, "_loaded_models_cache_time", 0)
            and (now - self._loaded_models_cache_time < 5.0)
            and self._loaded_models_cache is not None
        ):
            return self._loaded_models_cache

        res = list_loaded_sync(self._base_url)
        self._loaded_models_cache = res
        self._loaded_models_cache_time = now
        return res

    def _maybe_unload_after_use(self) -> None:
        """
        Free the 9B selector from LM Studio right after it answers, before the main model
        generates. Two models resident at once measurably slows the main model's tokens/sec —
        the reload cost next turn is worth paying to keep the main model at full speed.

        Eval harness turns (platform="eval") skip this: a live/workflow suite needs the
        selector again on the next case within seconds, and unload→evict-main→reload-9B→
        evict-9B→reload-main is the VRAM thrash we just measured (~20–30s dead gap per case).
        Dry-run already forces ``unload_after_select=False`` in config; this gate covers the
        live Brain path that still reads production config.

        Never unloads when we merely reused whatever model was already active (the fallback
        path that exists specifically to avoid evicting the user's main chat model) — this
        matters even more now that ``release`` only actually unloads once every owner's claim
        on the model is gone: if it's the main model, some other owner (e.g. ``turn_coordinator``)
        still holds it, so this call would be a no-op anyway, but we skip it explicitly for clarity.

        Fire-and-forget: select() must return the moment it has its answer — the supervisor
        doesn't need to wait on this network round-trip, so the release runs on a daemon
        thread instead of blocking the caller for the 2-10s it can take.
        """
        if not self._unload_after_select or not self._resolved_model_id:
            return
        # Pinned models stay resident by definition (local_models.lifecycle.pinned_models).
        # Unloading here would defeat the pin: the next turn reloads the selector, which under
        # the pin no longer evicts the main model — so we'd pay the reload and keep none of
        # the benefit. On a single-GPU box the pin is precisely the trade "slower selector,
        # no swap", and that only holds if the selector is never unloaded.
        try:
            from src.core.intelligence.model_lifecycle import is_pinned
            if is_pinned(self._resolved_model_id):
                logger.debug(
                    f"[ LLM_SELECTOR ] Skipping unload — '{self._resolved_model_id}' is pinned."
                )
                return
        except Exception:
            pass
        try:
            from src.core.orchestration.eval_exclusivity import eval_turns_active
            if eval_turns_active() > 0:
                logger.info(
                    "[ LLM_SELECTOR ] Skipping unload — eval turn active "
                    "(keep selector resident across cases)"
                )
                return
        except Exception:
            pass
        if self._main_model_hint and self._match_model_id(
            self._main_model_hint, [self._resolved_model_id]
        ):
            logger.debug(
                f"[ LLM_SELECTOR ] Skipping unload — '{self._resolved_model_id}' "
                "is the resident main model."
            )
            return
        if not self._match_model_id(self._model_id, [self._resolved_model_id]):
            return

        self._loaded_models_cache = None
        self._loaded_models_cache_time = 0

        resolved_model_id = self._resolved_model_id
        self._resolved_model_id = None

        def _background_release():
            release_sync(self._base_url, resolved_model_id, owner=self._OWNER)

        threading.Thread(target=_background_release, daemon=True, name="llm-selector-release").start()

    @staticmethod
    def _match_model_id(model_id: str, loaded_ids: List[str]) -> Optional[str]:
        """Match configured model id against LM Studio ids (handles :quant suffixes)."""
        if model_id in loaded_ids:
            return model_id
        base = model_id.split(":")[0]
        for loaded in loaded_ids:
            if loaded.split(":")[0] == base:
                return loaded
        return None

    # ------------------------------------------------------------------
    # Response handling
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_message_content(body: Dict[str, Any]) -> str:
        message = body["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        if content:
            return content
        # Some providers put the answer only in reasoning fields — ignore those.
        return ""

    @staticmethod
    def _strip_thinking(raw: str) -> str:
        # Qwen 3.x reasoning wrappers — strip closed and unclosed variants.
        think_open = chr(60) + "think" + chr(62)
        think_close = chr(60) + "/" + "think" + chr(62)
        raw = re.sub(
            think_open + r".*?" + think_close,
            "",
            raw,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        raw = re.sub(
            think_open + r".*$",
            "",
            raw,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        raw = re.sub(
            r"<think>.*?</think>",
            "",
            raw,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        raw = re.sub(
            r"<think>.*$",
            "",
            raw,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        return raw

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    # How much of each tool's description the selector sees. The selector can only pick the
    # right tool if it understands what the tool does, so keep enough to disambiguate similar
    # tools — but cap it so a full catalog doesn't blow the context. In hybrid mode the catalog
    # is a small RAG shortlist, so richer descriptions are cheap.
    _DESC_CHAR_CAP = 240

    def _build_catalog(self, tools: Dict[str, Any]) -> Dict[str, str]:
        """Extract name→concise-description from registered tools (enough to disambiguate)."""
        catalog: Dict[str, str] = {}
        for name, tool in tools.items():
            desc = ""
            if hasattr(tool, "description"):
                desc = str(tool.description)
            elif isinstance(tool, dict):
                desc = tool.get("description") or tool.get("schema", {}).get("description") or tool.get("inputSchema", {}).get("description") or ""
                desc = str(desc)
            desc = re.sub(r"\s+", " ", desc).strip()
            catalog[name] = self._concise_description(desc)
        return catalog

    @classmethod
    def _concise_description(cls, desc: str) -> str:
        """Keep the leading sentences up to the char cap — not just the first short fragment."""
        if len(desc) <= cls._DESC_CHAR_CAP:
            return desc
        # Take whole sentences until we'd exceed the cap; always return at least the cap prefix.
        out = ""
        for sentence in re.split(r"(?<=[.!?])\s+", desc):
            if not out:
                out = sentence
            elif len(out) + 1 + len(sentence) <= cls._DESC_CHAR_CAP:
                out += " " + sentence
            else:
                break
        return (out or desc)[:cls._DESC_CHAR_CAP].rstrip()

    def _parse(self, raw: str, valid_names: set) -> Optional[List[str]]:
        """Strip any wrapping, parse JSON array, validate names."""
        raw = self._strip_thinking(raw)
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()

        if not raw:
            return None

        if not raw.startswith("["):
            match = re.search(r"\[[^\]]*\]", raw, re.DOTALL)
            if match:
                raw = match.group(0)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"[ LLM_SELECTOR ] JSON parse failed: {e}. Raw: {raw!r}")
            return None

        if not isinstance(result, list):
            logger.warning(f"[ LLM_SELECTOR ] Expected list, got {type(result)}")
            return None

        valid = [str(t) for t in result if str(t) in valid_names]
        hallucinated = [str(t) for t in result if str(t) not in valid_names]
        if hallucinated:
            logger.warning(f"[ LLM_SELECTOR ] Dropped unknown tool names: {hallucinated}")

        return valid
