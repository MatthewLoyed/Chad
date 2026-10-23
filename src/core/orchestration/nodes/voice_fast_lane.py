"""voice_fast_lane.py — no-LLM quick-command router prototype for issue #654 problem 3.

The current voice lane (`voice_node.py`) routes EVERY quick command ("open youtube",
"search for pizza places") through a 9B model call before deciding what to do — that
model round-trip is the wrong latency class entirely (seconds, not milliseconds) for
what is, in practice, a fixed and narrow grammar: open an app/site, run a web search,
control media, or hand off to smart-home control.

This router matches that narrow grammar directly, in two tiers:
  1. Regex/grammar match — near-zero cost, handles the canonical phrasings.
  2. Embedding-similarity fallback (reuses the same `nomic-embed-text-v2-moe` model
     already loaded for tool-RAG, see `local_embeddings.py`) — handles paraphrases
     regex won't ("pull up the flight tracker", "throw on some music") without
     adding a new model dependency.

Anything below the similarity threshold returns `None`: the caller bails to the
existing dictation/agent lane immediately. This module makes no decision to escalate
slowly — a miss here should be a FAST miss, not a stall.

Wired via `execute_fast_lane` below into `turn/pipeline.py`'s
`TurnPipeline._try_voice_fast_lane`, which runs BEFORE mode resolution, knowledge
fetch, tool selection, and model preload — every `category="voice"` turn passes
through it before the graph (and `voice_node`) is ever reached, so a hit never
JIT-loads the 9B voice model at all. Deliberately NOT duplicated at the node level:
every voice turn already passes through this check first, so a second copy inside
`voice_node.py` could only ever re-confirm a miss already found — at the cost of
re-running the (up to ~1s) embedding-tier match a second time on every escalating
command.

Also benchmarkable directly via `evals/voice_latency_bench.py` against the ~440ms
fast-lane target from issue #654.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("VoiceFastLane")

ACTION_OPEN = "open"
ACTION_SEARCH = "web_search"
ACTION_MEDIA = "media"
ACTION_SMART_HOME = "smart_home"

# Polite wrappers STT often keeps ("can you open youtube", "open youtube please").
_POLITE_PREFIX = r"(?:(?:can you|could you|would you|will you|please)\s+)+"
_POLITE_SUFFIX = r"(?:\s+please)?"

# Regex tier: canonical phrasings, cheapest possible match.
_REGEX_ROUTES: List[Tuple[str, re.Pattern]] = [
    (ACTION_SMART_HOME, re.compile(
        rf"^(?:{_POLITE_PREFIX})?(?:turn (?:on|off)|dim|brighten|switch (?:on|off))\s+(?:the\s+)?"
        r".*\b(?:lights?|lamps?)\b" + _POLITE_SUFFIX + r"$", re.IGNORECASE)),
    (ACTION_SEARCH, re.compile(
        rf"^(?:{_POLITE_PREFIX})?(?:search(?: the web)?(?: for)?|google|look up)\s+(.+?)"
        + _POLITE_SUFFIX + r"$", re.IGNORECASE)),
    (ACTION_MEDIA, re.compile(
        rf"^(?:{_POLITE_PREFIX})?(?:play|resume|pause|skip|shuffle)\s*(.*?)"
        + _POLITE_SUFFIX + r"$", re.IGNORECASE)),
    (ACTION_OPEN, re.compile(
        rf"^(?:{_POLITE_PREFIX})?(?:go to|open|launch|pull up|start(?: up)?|fire up)\s+"
        r"(?:the\s+)?(.+?)" + _POLITE_SUFFIX + r"$",
        re.IGNORECASE)),
]

# Imperative open at utterance start (after optional polite wrapper). Used so the
# embedding override cannot treat "open" inside "find open source tools" as a
# launch command.
_OPEN_IMPERATIVE_RE = re.compile(
    rf"^(?:{_POLITE_PREFIX})?(?:go to|open|launch|pull up|start(?: up)?|fire up)\b",
    re.IGNORECASE,
)

# Embedding tier: paraphrase prototypes per action, used only when regex misses.
_EMBED_PROTOTYPES = {
    ACTION_OPEN: [
        "open the browser to a website",
        "launch this application",
        "pull up a website or app",
        "switch over to this program",
    ],
    ACTION_SEARCH: [
        "search the web for something",
        "look something up online",
        "find information about a topic on the internet",
    ],
    ACTION_MEDIA: [
        "play some music",
        "control music or video playback",
        "put on a song or playlist",
    ],
    ACTION_SMART_HOME: [
        "turn the lights on or off",
        "control a smart home device",
        "dim or brighten a lamp",
    ],
}

DEFAULT_SIMILARITY_THRESHOLD = 0.55


@dataclass
class FastLaneMatch:
    action: str
    slot: str
    method: str  # "regex" | "embedding"
    confidence: float
    latency_s: float


def _clean_slot(slot: str) -> str:
    """Strip trailing polite fluff STT leaves on the captured target."""
    cleaned = re.sub(r"\s+please\s*$", "", slot.strip(), flags=re.IGNORECASE)
    return cleaned.strip(" .,!?").strip()


def _regex_match(text: str) -> Optional[FastLaneMatch]:
    stripped = text.strip()
    for action, pattern in _REGEX_ROUTES:
        m = pattern.match(stripped)
        if m:
            slot = _clean_slot(m.group(1)) if m.groups() and m.group(1) else ""
            return FastLaneMatch(action=action, slot=slot, method="regex",
                                  confidence=1.0, latency_s=0.0)
    return None


def template_filler_for(text: str) -> Optional[str]:
    """Instant, no-LLM first-speech filler keyed off the cheap regex tier.

    Returns ``None`` for open matches (caller should skip fillers entirely —
    the real ack *is* first speech) or when nothing classifies.
    """
    hit = _regex_match(text)
    if hit is None:
        return None
    if hit.action == ACTION_OPEN:
        return None
    if hit.action == ACTION_SEARCH:
        return "Looking that up."
    if hit.action == ACTION_MEDIA:
        return "Starting that."
    if hit.action == ACTION_SMART_HOME:
        return "Working on that."
    return None


def is_instant_open_command(text: str) -> bool:
    """True when tier-1 regex confidently classifies an open — no filler race."""
    hit = _regex_match(text)
    return hit is not None and hit.action == ACTION_OPEN


class EmbeddingRouter:
    """Lazily embeds prototypes once, then scores each incoming text by cosine sim."""

    def __init__(self, embedding_model=None):
        self._model = embedding_model
        self._prototype_vecs: Optional[dict] = None

    def _ensure_model(self):
        if self._model is None:
            from src.core.intelligence.local_embeddings import get_embedding_model
            self._model = get_embedding_model()
        return self._model

    def _ensure_prototypes(self):
        if self._prototype_vecs is not None:
            return
        model = self._ensure_model()
        vecs = {}
        for action, phrases in _EMBED_PROTOTYPES.items():
            embs = model.encode(phrases)
            vecs[action] = embs
        self._prototype_vecs = vecs

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def match(self, text: str, threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> Optional[FastLaneMatch]:
        self._ensure_prototypes()
        model = self._ensure_model()
        query_vec = model.encode(text)

        best_action, best_score = None, -1.0
        scores: dict = {}
        for action, proto_vecs in self._prototype_vecs.items():
            action_best = -1.0
            for proto_vec in proto_vecs:
                score = self._cosine(query_vec, proto_vec)
                if score > action_best:
                    action_best = score
                if score > best_score:
                    best_action, best_score = action, score
            scores[action] = action_best

        # Imperative open at the start must not lose to media/search paraphrases.
        # Require utterance-leading open verbs so "find open source tools" stays
        # out of ACTION_OPEN.
        if _OPEN_IMPERATIVE_RE.search(text):
            open_score = scores.get(ACTION_OPEN, -1.0)
            if open_score >= threshold:
                best_action, best_score = ACTION_OPEN, open_score

        if best_action is None or best_score < threshold:
            return None
        return FastLaneMatch(action=best_action, slot=text.strip(), method="embedding",
                              confidence=best_score, latency_s=0.0)


# Issue #654 problem 3: only actions with NO information payload to relay are
# actually executed here — "open X" is complete the instant the browser/app opens,
# there's nothing to tell the user afterward. media ("play some music") and
# smart_home need slot resolution (song/device lookup) the model currently does
# for free — wiring those is a follow-up.
#
# ACTION_SEARCH is deliberately NOT here even though the router still classifies
# it: live testing showed "Searching for X." as a fire-and-forget ack is actively
# broken for search — the whole point of a search is the answer, and this tier
# has no model call to produce one. A miss here for a search-shaped phrase falls
# through to tier 2 (`voice_quick_tool.py`), which does the tool call AND feeds
# the results back through a synthesis step to actually answer the question.
FAST_LANE_TOOL_BY_ACTION = {
    ACTION_OPEN: ("open_browser", lambda slot: {"query": slot}),
}
FAST_LANE_ACK = {
    ACTION_OPEN: "Opening {slot}.",
}


async def execute_fast_lane(text: str, tool_registry: Optional[Any]) -> Optional[str]:
    """Match + execute a quick voice command with no LLM call.

    Returns the ack text to speak on a handled hit, or ``None`` to fall through to
    the normal turn pipeline (ambiguous request, unwired action, missing tool, or a
    tool-call failure — always fails OPEN, never fails closed).
    """
    hit = match_fast_lane(text)
    if hit is None or hit.action not in FAST_LANE_TOOL_BY_ACTION:
        return None
    if tool_registry is None:
        return None

    tool_name, build_args = FAST_LANE_TOOL_BY_ACTION[hit.action]
    tool = tool_registry.get_langchain_tool_by_name(tool_name)
    if not tool:
        logger.warning(
            f"[ VOICE FAST LANE ] tool '{tool_name}' not found in registry "
            f"(action={hit.action!r}); falling through to the normal turn."
        )
        return None

    try:
        await tool.ainvoke(build_args(hit.slot))
    except Exception as e:
        logger.warning(
            f"[ VOICE FAST LANE ] tool '{tool_name}' failed ({e!r}) for {hit.slot!r}; "
            "falling through to the normal turn."
        )
        return None

    logger.info(
        f"[ VOICE FAST LANE ] {hit.method} match -> {tool_name}({hit.slot!r}) "
        f"in {hit.latency_s * 1000:.1f}ms"
    )
    return FAST_LANE_ACK[hit.action].format(slot=hit.slot)


_default_embedding_router: Optional[EmbeddingRouter] = None


def _get_default_embedding_router() -> EmbeddingRouter:
    global _default_embedding_router
    if _default_embedding_router is None:
        _default_embedding_router = EmbeddingRouter()
    return _default_embedding_router


def match_fast_lane(
    text: str,
    *,
    embedding_router: Optional[EmbeddingRouter] = None,
    use_embedding_fallback: bool = True,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Optional[FastLaneMatch]:
    """Route one transcript. Returns None => caller should bail to dictation/agent lane."""
    t0 = time.perf_counter()

    hit = _regex_match(text)
    if hit is not None:
        hit.latency_s = time.perf_counter() - t0
        return hit

    if not use_embedding_fallback:
        return None

    router = embedding_router or _get_default_embedding_router()
    try:
        hit = router.match(text, threshold=similarity_threshold)
    except (OSError, ImportError, RuntimeError):
        # Optional embedding assets may be uncached on a fresh/offline install.
        # Preserve regex quick commands and fall through to the normal voice
        # path instead of aborting the turn or initiating a download.
        return None
    if hit is not None:
        hit.latency_s = time.perf_counter() - t0
    return hit
