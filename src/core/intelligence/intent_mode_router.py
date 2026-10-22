"""
Intent mode router — tiny local LLM (Gemma 1B) classifies each turn into a lane
before tool selection, RAG, or the full supervisor graph run.

Modes:
  chat     — casual conversation, no tools, minimal prompt
  command  — single direct action
  agent    — full agent pipeline (default fallback)
  planning — multi-step project / plan-first requests
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.core.intelligence.model_lifecycle import (
    ensure_resident_sync, release_sync, current_priority, active_use_sync,
)
from src.shared.turn_mode import normalize_turn_mode
from src.shared.logger import setup_logger

logger = setup_logger("IntentModeRouter")

VALID_MODES = frozenset({"chat", "command", "agent", "planning"})

SYSTEM_PROMPT = """\
You are an intent router for an AI assistant. Classify the user's latest message into exactly one mode.

Modes:
- chat: greetings, small talk, thanks, casual conversation — no tools or actions needed
- command: exactly ONE immediate, direct system action with no research ("open discord", "set volume to 50", "mute the mic")
- agent: research, coding, email/file search, analysis, questions needing tools, reading or summarizing content, any task requiring reasoning or multiple steps
- planning: user wants a structured multi-step plan or project before execution ("build me a website", "plan a trip", "refactor the auth module")

Rules:
- Email search, file lookup, coding, research, analysis → agent (NOT command)
- Multi-step builds or projects → planning (NOT command)
- If the message explicitly asks to "create/make/put together/come up with a plan" (a plan is the deliverable), choose planning — even if the plan's steps involve research, comparison, or analysis. The presence of "research"/"compare"/"analyze" does NOT override an explicit plan request.
- When unsure between command and agent, choose agent
- When unsure between agent and planning, choose planning only if the user clearly wants a project plan built first

Examples:
"hi" → {"mode":"chat","confidence":0.98}
"thanks!" → {"mode":"chat","confidence":0.98}
"open discord" → {"mode":"command","confidence":0.98}
"set volume to 50" → {"mode":"command","confidence":0.98}
"search my email for invoices from last week" → {"mode":"agent","confidence":0.98}
"refactor the auth module" → {"mode":"agent","confidence":0.98}
"build me a wedding website with RSVP and photo gallery" → {"mode":"planning","confidence":0.98}
"create a plan to research and compare three project management tools for our team" → {"mode":"planning","confidence":0.95}

Reply with ONLY raw JSON, no markdown:
{"mode":"chat","confidence":0.95}"""


@dataclass(frozen=True)
class IntentClassification:
    mode: str
    confidence: float
    raw: str = ""
    latency_s: float = 0.0

    @property
    def is_chat(self) -> bool:
        return self.mode == "chat"


class IntentModeRouter:
    """Calls a tiny always-hot LM Studio model to pick the turn lane."""

    def __init__(
        self,
        model_id: str,
        base_url: str = "http://localhost:1234/v1",
        *,
        fallback_mode: str = "agent",
        strict_model: bool = True,
    ):
        self._model_id = model_id
        self._fallback_mode = fallback_mode if fallback_mode in VALID_MODES else "agent"
        self._strict_model = strict_model
        self._base_url = base_url.rstrip("/")
        self._completions_url = self._base_url + "/chat/completions"
        self._models_url = self._base_url + "/models"
        self._resolved_model_id: Optional[str] = None
        self._server_reachable: Optional[bool] = None
        self._unavailable_reason: Optional[str] = None
        self._last_meta: Dict[str, Any] = {}

    @classmethod
    def from_config(cls) -> Optional["IntentModeRouter"]:
        from src.shared.config_manager import ConfigManager

        cfg = ConfigManager.get("intent_router") or {}
        if not cfg.get("enabled", True):
            return None

        lm = ConfigManager.get_local_provider_config("lmstudio")
        model_id = cfg.get("model") or "google/gemma-3-1b"
        return cls(
            model_id=model_id,
            base_url=cfg.get("base_url") or lm.get("base_url", "http://localhost:1234/v1"),
            fallback_mode=cfg.get("fallback_mode", "agent"),
            strict_model=bool(cfg.get("strict_model", True)),
        )

    @property
    def last_meta(self) -> Dict[str, Any]:
        return dict(self._last_meta)

    def warmup(self) -> bool:
        ok = self._ensure_model_loaded()
        try:
            from src.shared.startup_timing import set_flag

            if ok:
                set_flag("intent_mode_router", f"reachable, model={self._resolved_model_id}")
            else:
                set_flag("intent_mode_router", f"UNREACHABLE ({self._unavailable_reason})")
        except Exception:
            pass
        return ok

    _OWNER = "intent_router"

    def unload(self) -> None:
        """Release our claim on the gemma model after classification — the coordinator only
        actually unloads once no other owner (e.g. the main model, if we reused it) wants it."""
        model_id = self._resolved_model_id or self._model_id
        # Never unload the supervisor main model when we reused it for classification.
        if not self._match_model_id(self._model_id, [model_id]):
            return
        self._resolved_model_id = None
        release_sync(self._base_url, model_id, owner=self._OWNER)

    def classify(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> IntentClassification:
        """Return the lane for this turn. Falls back to ``fallback_mode`` on any failure."""
        self._last_meta = {"query": (query or "")[:120]}

        if not (query or "").strip():
            return IntentClassification(mode=self._fallback_mode, confidence=0.0)

        if not self._ensure_model_loaded():
            self._last_meta.update({"status": "fallback", "reason": self._unavailable_reason})
            logger.warning(
                f"[ INTENT_ROUTER ] Unavailable ({self._unavailable_reason}) — "
                f"defaulting to {self._fallback_mode}"
            )
            return IntentClassification(mode=self._fallback_mode, confidence=0.0)

        user_msg = self._build_user_message(query, history)

        def _build_request() -> urllib.request.Request:
            model = self._resolved_model_id or self._model_id
            payload: Dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.0,
                "max_tokens": 64,
            }
            return urllib.request.Request(
                self._completions_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

        t0 = time.perf_counter()
        # Mirrors llm_selector.py's retry: LM Studio can report the model resident and then
        # evict it (VRAM-budget eviction, idle TTL) before this request lands. Force a fresh
        # residency check and retry once instead of silently degrading every such turn to the
        # fallback mode.
        max_attempts = 2
        raw = None
        for attempt in range(1, max_attempts + 1):
            model = self._resolved_model_id or self._model_id
            req = _build_request()
            try:
                with active_use_sync(self._base_url, model):
                    with urllib.request.urlopen(req, timeout=30.0) as resp:
                        body = json.loads(resp.read())
                        raw = self._extract_content(body)
                break
            except urllib.error.HTTPError as e:
                try:
                    err_body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    err_body = ""
                if attempt < max_attempts and "unload" in err_body.lower():
                    logger.warning(
                        f"[ INTENT_ROUTER ] '{model}' reported unloaded mid-request "
                        f"(attempt {attempt}/{max_attempts}) — forcing reload and retrying."
                    )
                    self._resolved_model_id = None
                    if self._ensure_model_loaded():
                        continue
                latency = time.perf_counter() - t0
                reason = f"{e} — {err_body}"
                self._last_meta.update({"status": "fallback", "reason": reason, "latency_s": round(latency, 2)})
                logger.warning(f"[ INTENT_ROUTER ] Classify failed ({reason}) — defaulting to {self._fallback_mode}")
                return IntentClassification(mode=self._fallback_mode, confidence=0.0, latency_s=latency)
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError, IndexError) as e:
                latency = time.perf_counter() - t0
                self._last_meta.update({"status": "fallback", "reason": str(e), "latency_s": round(latency, 2)})
                logger.warning(f"[ INTENT_ROUTER ] Classify failed ({e}) — defaulting to {self._fallback_mode}")
                return IntentClassification(mode=self._fallback_mode, confidence=0.0, latency_s=latency)

        latency = time.perf_counter() - t0
        parsed = self._parse(raw)
        if parsed is None:
            self._last_meta.update({"status": "parse_failed", "raw": raw[:200], "latency_s": round(latency, 2)})
            logger.warning(
                f"[ INTENT_ROUTER ] Could not parse {raw[:120]!r} — defaulting to {self._fallback_mode}"
            )
            return IntentClassification(
                mode=self._fallback_mode, confidence=0.0, raw=raw, latency_s=latency
            )

        mode, confidence = parsed
        canonical = normalize_turn_mode(mode)
        self._last_meta.update(
            {"status": "ok", "mode": canonical, "raw_mode": mode, "confidence": confidence, "latency_s": round(latency, 2)}
        )
        logger.info(f"[ INTENT_ROUTER ] '{query[:60]}' → {canonical} ({confidence:.2f}, {latency:.2f}s)")
        return IntentClassification(mode=canonical, confidence=confidence, raw=raw, latency_s=latency)

    @staticmethod
    def _build_user_message(query: str, history: Optional[List[Dict[str, Any]]]) -> str:
        if not history:
            return f'Latest user message: "{query}"'

        from src.core.intelligence.query_synthesizer import QuerySynthesizer

        enriched = QuerySynthesizer.enrich_with_history(query, history, max_turns=2)
        if enriched != query:
            return (
                f"Recent conversation (context only):\n{enriched}\n\n"
                f'Latest user message to classify: "{query}"'
            )
        return f'Latest user message: "{query}"'

    def _parse(self, raw: str) -> Optional[tuple[str, float]]:
        text = (raw or "").strip()
        if not text:
            return None

        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[^{}]*\"mode\"[^{}]*\}", text)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None

        mode = str(data.get("mode", "")).lower().strip()
        if mode not in VALID_MODES:
            return None

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return mode, confidence

    @staticmethod
    def _extract_content(body: Dict[str, Any]) -> str:
        choice = body["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            content = "".join(parts)
        return str(content).strip()

    def _ensure_model_loaded(self) -> bool:
        if not self._is_server_reachable():
            self._unavailable_reason = "lm_studio_unreachable"
            return False

        # Read once here (same thread/task as the caller) so an autonomous/heartbeat turn's
        # claim queues behind an active interactive turn. See model_lifecycle.current_priority().
        priority = current_priority()

        loaded = self._list_loaded_models()
        gemma_matched = self._match_model_id(self._model_id, loaded)
        if gemma_matched:
            ensure_resident_sync(self._base_url, gemma_matched, owner=self._OWNER, priority=priority)
            self._resolved_model_id = gemma_matched
            self._unavailable_reason = None
            return True

        if self._strict_model:
            if ensure_resident_sync(self._base_url, self._model_id, owner=self._OWNER, priority=priority):
                self._resolved_model_id = self._model_id
                self._unavailable_reason = None
                return True
            self._unavailable_reason = f"model_not_loaded:{self._model_id}"
            return False

        if loaded:
            self._resolved_model_id = loaded[0]
            ensure_resident_sync(self._base_url, self._resolved_model_id, owner=self._OWNER, priority=priority)
            self._unavailable_reason = None
            return True

        self._unavailable_reason = "no_models_loaded"
        return False

    @staticmethod
    def _match_model_id(model_id: str, loaded_ids: List[str]) -> Optional[str]:
        if model_id in loaded_ids:
            return model_id
        base = model_id.split(":")[0]
        for loaded in loaded_ids:
            if loaded.split(":")[0] == base:
                return loaded
        return None

    def _is_server_reachable(self) -> bool:
        if self._server_reachable is True:
            return True
        try:
            with urllib.request.urlopen(self._models_url, timeout=3.0) as resp:
                self._server_reachable = resp.status == 200
        except Exception:
            self._server_reachable = False
        return bool(self._server_reachable)

    def _list_loaded_models(self) -> List[str]:
        try:
            with urllib.request.urlopen(self._models_url, timeout=5.0) as resp:
                data = json.loads(resp.read())
            return [m.get("id") for m in data.get("data", []) if m.get("id")]
        except Exception:
            return []

