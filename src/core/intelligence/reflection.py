r"""
reflection.py — runtime core for Reflective Lessons (Chad teaches himself).

This promotes the validated reflection core out of the read-only eval probe
(evals/reflect_probe.py) into a runtime-callable module, and adds Step 3 from
docs/DESIGN_reflective-lessons.md: wiring admitted lessons into Chad's persona.

The pipeline, off the hot path:

    recent conversations + tool RESULTS
              │
              ▼   reflect()  — judge proposes scoped candidate lessons + questions
        candidate lessons / open questions
              │
              ▼   LessonGatekeeper.filter()  — deterministic admission guards
        admitted (provisional) lessons
              │
              ▼   apply_admitted()  — append to a delimited `## Learned Lessons`
        block in the `user` / `tools` persona_config records (versioned, reversible)

CRITICAL SAFETY POSTURE (this is the FIRST code path that writes to Chad's live
behavior): the write is gated. `propose_lessons()` is pure/read-only and returns a
diff. `apply_admitted()` performs the write but is only reached when the caller
explicitly asks (CLI `--write` + a typed confirmation). Nothing here writes on
import or by default. persona_config is versioned, so every write is rollback-able;
we only ever APPEND to the lessons block, never destructive-write the record.

CLI:
    # dry-run — reflect, gate, print the diff. NOTHING is written.
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m src.core.intelligence.reflection \
        --convos 15 --model "qwen/qwen3.6-27b"
    # actually write the admitted lessons (asks for typed confirmation first)
    ... -m src.core.intelligence.reflection --convos 15 --model "qwen/qwen3.6-27b" --write
    # GC pass (#640): list expired/outage-shaped/duplicate lessons already written to
    # the `lessons` persona record. Read-only unless --write is also passed.
    ... -m src.core.intelligence.reflection --prune
    ... -m src.core.intelligence.reflection --prune --write
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("Reflection")

# Judge prompt + log shaping live in the eval probe (validated there). We import
# them so there is ONE source of truth for the reflection rubric and the log format.
# (reflect_probe re-exports the moved helpers from this module — see the bottom.)

# Where lessons are filed, by judge scope:
#   user  → the KNOWLEDGE GRAPH (Chad's one consolidated personal memory) via
#           GraphRAGClient.ingest — so every fact about the user lives in one store.
#   tools → the dedicated `lessons` persona fragment's `## Learned Lessons` block
#           (operational lessons aren't personal facts, so the graph is not their home).
#
# `tools` lessons used to be filed into the `tools` fragment itself. That put unattended
# writes and hand-authored doctrine in one file, and a write that replaced rather than
# merged left the live persona with a lessons list and NO tool-calling doctrine — Chad
# had no guidance about when to call a tool at all, invisibly, for weeks. Lessons now
# own a fragment nothing else writes, so the blast radius of a bad auto-write is the
# lessons themselves.
PERSONA_SCOPES = {"tools": "lessons"}   # scope → persona fragment id
GRAPH_SCOPES = {"user"}               # scopes routed to the knowledge graph

# Delimited, auditable, prunable block — kept separate from hand-written persona so
# the lesson GC / refiner pass (docs/DESIGN, "lesson lifecycle") can manage it alone.
LESSON_BLOCK_HEADER = "## Learned Lessons"
_LESSON_BLOCK_NOTE = (
    "<!-- Auto-authored by Chad's reflection loop. Each line is a durable, grounded "
    "lesson admitted by LessonGatekeeper. Safe to prune. -->"
)


# ──────────────────────────────────────────────────────────────────────────────
# Reflection log shaping (moved from evals/reflect_probe.py — single source of truth)
# ──────────────────────────────────────────────────────────────────────────────

def _msg_text(content: Any) -> str:
    """messages.content is sometimes a JSON list of {type,text} blocks, sometimes a str."""
    if isinstance(content, str):
        if content.startswith("[") or content.startswith("{"):
            try:
                content = json.loads(content)
            except Exception:
                return content
        else:
            return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(blk.get("text", ""))
            elif isinstance(blk, str):
                parts.append(blk)
        return " ".join(p for p in parts if p)
    if isinstance(content, dict):
        return content.get("text", "") or json.dumps(content)[:300]
    return str(content)


def _tool_events(meta: Any) -> List[str]:
    """Extract tool calls + their RESULTS from a message's metadata.thoughts trace.

    Tool RESULTS are the grounding the judge needs to write FACTS (e.g. list_devices
    -> 'bedroom') instead of harmful 'stop asking' lessons.
    """
    if not isinstance(meta, dict):
        return []
    thoughts = meta.get("thoughts")
    if not isinstance(thoughts, list):
        return []
    out: List[str] = []
    pending: Dict[str, dict] = {}
    for th in thoughts:
        if not isinstance(th, dict):
            continue
        ty = th.get("type")
        if ty == "tool_start":
            cid = th.get("correlation_id") or th.get("name") or str(len(out))
            pending[cid] = th
        elif ty == "tool_end":
            cid = th.get("correlation_id")
            start = pending.pop(cid, {}) if cid else {}
            name = th.get("name") or start.get("name") or "tool"
            params = start.get("params") or {}
            param_str = ", ".join(f"{k}={v}" for k, v in params.items()
                                  if v not in (None, "", [], {}))[:160]
            result = th.get("result")
            if isinstance(result, dict):
                ok = result.get("success", th.get("success"))
                gist = result.get("error") or result.get("result") or result.get("data") or result
                res_str = json.dumps(gist, default=str)[:220] if not isinstance(gist, str) else gist[:220]
            else:
                ok = th.get("success")
                res_str = (str(result) if result is not None else "")[:220]
            verdict = "OK" if ok else "FAILED"
            out.append(f"[tool] {name}({param_str}) -> {verdict}: {res_str}")
    return out[:8]


def load_reflection_log(convos: int, max_msgs_per_convo: int = 12) -> str:
    """Pull recent conversations + tool results + recent tool failures into a compact log."""
    from src.infrastructure.database.supabase_manager import get_supabase_manager
    db = get_supabase_manager()
    if not db.enabled:
        raise RuntimeError("Supabase not enabled — check .env")

    convs = db.query("conversations", select="id,title,updated_at,domain",
                     order="updated_at.desc", limit=convos)
    blocks: List[str] = []
    for c in convs:
        # Skip domain-scoped threads entirely. Reflection runs offline via asyncio.run,
        # outside any turn, so domain_var is unset and every lesson it writes lands in the
        # GLOBAL tier — distilling a Financial thread here would republish exactly the
        # private content domain agents exist to contain. Per-domain reflection is a
        # deliberate follow-up; until then this fails closed.
        if (c.get("domain") or "").strip():
            continue
        msgs = db.query("messages", select="role,content,metadata,created_at",
                        filters={"conversation_id": f"eq.{c['id']}"},
                        order="created_at.asc", limit=max_msgs_per_convo)
        if not msgs:
            continue
        lines = [f"### Conversation: {c.get('title') or c['id']}"]
        for m in msgs:
            role = m.get("role", "?")
            text = _msg_text(m.get("content"))[:500]
            meta = m.get("metadata") or {}
            rating = meta.get("user_rating")
            tag = f" [user rated: {rating}]" if rating else ""
            lines.append(f"- {role}{tag}: {text}")
            for ev in _tool_events(meta):
                lines.append(f"    {ev}")
        blocks.append("\n".join(lines))

    logs = db.query("tool_outcome_log", select="tool_name,query_text,outcome,user_rating",
                    order="created_at.desc", limit=80)
    if logs:
        fail_lines = [f"- {l['tool_name']} | {l.get('outcome')} | "
                      f"q='{(l.get('query_text') or '')[:80]}'"
                      + (f" [rated {l['user_rating']}]" if l.get("user_rating") else "")
                      for l in logs if l.get("outcome") == "failure" or l.get("user_rating") == "negative"]
        if fail_lines:
            blocks.append("### Recent tool FAILURES / negative ratings\n" + "\n".join(fail_lines[:30]))

    return "\n\n".join(blocks)


REFLECT_SYSTEM = r"""You are a reflection module for a personal AI assistant named Chad, used by ONE user. You are given recent conversation history and recent tool failures. Your job: spot RECURRING patterns and turn them into durable lessons that make Chad more efficient or more PERSONALIZED next time.

The log includes `[tool] name(params) -> OK/FAILED: result` lines. These tool RESULTS
are your best source of grounded FACTS — mine them.

=== TWO KINDS OF OUTPUT ===
(1) LESSON — a durable, grounded fact you can write into Chad's memory now.
(2) QUESTION — when Chad keeps hitting an uncertainty it CANNOT resolve from the
    evidence alone, ask the user. Their answer will become a lesson later. Use this
    instead of guessing.

=== WHAT MAKES A GOOD LESSON ===
A lesson must CARRY KNOWLEDGE, never SUPPRESS a behavior. NEVER write "stop asking",
"don't clarify", or "control it without asking" — that just makes Chad guess wrong
silently. Instead, state the resolving FACT. If you don't have the fact, emit a
QUESTION, not a suppression lesson.
The BEST lessons are CONCRETE and SPECIFIC to the user or their environment — not generic
advice. Good = (a) GENERALIZABLE; (b) DURABLE; (c) ACTIONABLE; (d) SPECIFIC (names the
actual device/preference/workflow), and grounded in a tool result or a clear pattern.

Lesson scope (pick one): "user" = a fact/preference about the user themselves; "tools" =
an operational/procedural lesson or an environment fact (e.g. which device a command maps to).

=== EXAMPLES ===
GOOD LESSON (grounded in a tool result):
  {"kind":"lesson","scope":"tools","lesson":"The user's lamp is registered in hubspace_control as 'bedroom' — use that device_name for 'my lamp'.","evidence":"[tool] list_devices -> OK: ['bedroom']; earlier turn_on with device_name='lamp' FAILED.","confidence":0.85}
  {"kind":"lesson","scope":"user","lesson":"The user prefers the conclusion/answer first, then supporting detail.","evidence":"...","confidence":0.7}
GOOD QUESTION (uncertainty not resolvable from evidence):
  {"kind":"question","question":"You said 'my lamp' but hubspace only lists a device called 'bedroom' — is that your lamp, so I should map 'my lamp' to it?","why":"turn_on with device_name='lamp' failed repeatedly and no device is literally named 'lamp'.","confidence":0.8}
  {"kind":"question","question":"You've mentioned a person by name several times — what's your relationship with them? I'll remember it.","why":"The same name recurs across turns with no stated relationship.","confidence":0.7}
BAD (reject — generic, or a SUPPRESSION lesson that carries no fact):
  "Clarify ambiguous requests."  |  "Don't ask the user for the device name."  |  "Break tasks into smaller steps."  |  "Be helpful."

=== DISCIPLINE ===
Be conservative. ABSTAIN by default — returning an empty list is correct and expected
when nothing concrete recurs. Do NOT invent items to fill space.

=== ALREADY KNOWN (read before proposing) ===
You will receive an ALREADY KNOWN block listing facts Chad already stores: personal
knowledge / knowledge-graph facts, persona ## Learned Lessons, pending or recently
applied reflection queue items, and the live tool catalog.

Rules:
- If a candidate lesson is semantically equivalent to an item in ALREADY KNOWN with no
  meaningful new context, do NOT emit it.
- An entity mentioned in existing memory does NOT mean every fact about that entity is
  captured — only skip when the specific fact itself is already covered.
- Questions already pending in the queue should not be re-asked unless the evidence
  materially changes the uncertainty.

Respond with ONLY this JSON, no prose, no markdown fences, no <think> tags:
{"items": [
  {"kind":"lesson","scope":"user|tools","lesson":"<terse SPECIFIC fact>","evidence":"<log moment / tool result>","confidence":<0.0-1.0>},
  {"kind":"question","question":"<what to ask the user>","why":"<the unresolved uncertainty>","confidence":<0.0-1.0>}
]}
Return {"items": []} if nothing clears the bar."""


@dataclass
class ReflectionResult:
    log_chars: int = 0
    model: str = ""
    lessons: List[dict] = field(default_factory=list)
    questions: List[dict] = field(default_factory=list)
    empty_output: bool = False
    raw: str = ""
    error: str = ""


def _load_personal_knowledge_texts() -> List[str]:
    """Canonical user facts from config/personal_knowledge.md (## sections)."""
    import re
    from src.shared.paths import get_project_root

    path = get_project_root() / "config" / "personal_knowledge.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    out: List[str] = []
    for section in re.split(r"^## ", text, flags=re.MULTILINE):
        if not section.strip() or section.startswith("#"):
            continue
        lines = section.strip().splitlines()
        topic = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        if body:
            out.append(f"{topic}: {body}")
        elif topic:
            out.append(topic)
    return out


async def _load_graph_fact_texts() -> List[str]:
    """All valid knowledge-graph node contents (Chad's live personal memory)."""
    try:
        from src.core.intelligence.graph_rag_client import GraphRAGClient
        graph = GraphRAGClient()
        await graph.initialize()
        return [
            (data.get("content") or "").strip()
            for _, data in graph._graph.nodes(data=True)
            if data.get("valid", True) and (data.get("content") or "").strip()
        ]
    except Exception:
        return []


async def load_already_known_context() -> Dict[str, List[str]]:
    """Load canonical memory the reflection judge should see before proposing."""
    from src.infrastructure.repositories.memory_repository import MemoryRepository

    ctx: Dict[str, List[str]] = {
        "personal_knowledge": _load_personal_knowledge_texts(),
        "graph_facts": await _load_graph_fact_texts(),
        "persona_lessons": [],
        "queue_lessons": [],
        "tools": fetch_available_tools(),
    }

    try:
        repo = MemoryRepository()
        for pid in set(PERSONA_SCOPES.values()):
            ctx["persona_lessons"].extend(
                extract_existing_lessons(repo.get_persona_config(pid) or ""))
    except Exception as e:
        logger.warning("persona lessons unavailable for reflection context: %s", e)

    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        from src.core.intelligence.reflection_lessons_store import existing_pending_lesson_texts
        supabase = get_supabase_manager()
        if supabase and supabase.enabled:
            ctx["queue_lessons"] = await existing_pending_lesson_texts(supabase)
    except Exception as e:
        logger.warning("queue lessons unavailable for reflection context: %s", e)

    return ctx


def dedup_corpus_from_context(ctx: Dict[str, List[str]]) -> List[str]:
    """Texts for gatekeeper / queue dedup — persona + queue + graph (or personal md)."""
    corpus: List[str] = []
    corpus.extend(ctx.get("persona_lessons") or [])
    corpus.extend(ctx.get("queue_lessons") or [])
    graph = ctx.get("graph_facts") or []
    if graph:
        corpus.extend(graph)
    else:
        corpus.extend(ctx.get("personal_knowledge") or [])
    return corpus


def filter_queue_duplicates(items: List[dict], queue: List[str], *, field: str) -> List[dict]:
    """Drop items whose `field` text near-matches an existing queue row."""
    if not items or not queue:
        return list(items or [])
    from src.core.intelligence.reflection_lessons_store import is_duplicate_lesson
    kept: List[dict] = []
    for it in items:
        text = (it.get(field) or "").strip()
        if text and is_duplicate_lesson(text, queue):
            logger.info("[ REFLECTION ] Dropping duplicate %s (queue match): %r", field, text[:80])
            continue
        kept.append(it)
    return kept


def format_already_known_block(ctx: Dict[str, List[str]]) -> str:
    """Render ALREADY KNOWN context for the reflection judge prompt."""
    sections: List[str] = []

    pk = ctx.get("personal_knowledge") or []
    if pk:
        sections.append("### Personal knowledge (canonical markdown)\n" +
                         "\n".join(f"- {t}" for t in pk[:40]))

    gf = ctx.get("graph_facts") or []
    if gf:
        sections.append("### Knowledge graph facts\n" +
                         "\n".join(f"- {t}" for t in gf[:60]))

    pl = ctx.get("persona_lessons") or []
    if pl:
        sections.append("### Persona ## Learned Lessons (operational)\n" +
                         "\n".join(f"- {t}" for t in pl[:40]))

    ql = ctx.get("queue_lessons") or []
    if ql:
        sections.append("### Reflection review queue (pending / recently applied)\n" +
                         "\n".join(f"- {t}" for t in ql[:40]))

    tools = ctx.get("tools") or []
    if tools:
        sections.append("### Live tool catalog\n" + ", ".join(sorted(tools)[:120]))

    return "\n\n".join(sections) if sections else "(no canonical memory loaded)"


async def reflect(convos: int, model: str, *,
                  already_known_ctx: Optional[Dict[str, List[str]]] = None) -> ReflectionResult:
    """Run the reflection judge over recent history. Read-only — proposes only."""
    from evals.judges import build_judge_adapter, _extract_json_object

    log = load_reflection_log(convos)
    if not log.strip():
        return ReflectionResult(error="no conversation history found")

    ctx = already_known_ctx if already_known_ctx is not None else await load_already_known_context()
    already_known = format_already_known_block(ctx)
    adapter = build_judge_adapter(model)
    messages = [
        {"role": "system", "content": REFLECT_SYSTEM},
        {"role": "user", "content": (
            f"ALREADY KNOWN:\n\n{already_known}\n\n"
            f"RECENT HISTORY LOG:\n\n{log}"
        )},
    ]

    # Reasoning models can spend the whole token budget "thinking" and emit an EMPTY
    # visible answer on larger logs. Keep thinking modest, give output room, retry once.
    async def _ask(max_tokens: int, thinking: str):
        return await adapter.generate(messages=messages, temperature=0.0,
                                      thinking_level=thinking, max_tokens=max_tokens)

    resp = await _ask(max_tokens=6000, thinking="low")
    if not (resp.text or "").strip():
        resp = await _ask(max_tokens=9000, thinking="low")

    data = _extract_json_object(resp.text or "")
    items = []
    if isinstance(data, dict):
        items = data.get("items") or [{"kind": "lesson", **l} for l in (data.get("lessons") or [])]
    if not isinstance(items, list):
        items = []
    lessons = [it for it in items if isinstance(it, dict) and it.get("kind") != "question"]
    questions = [it for it in items if isinstance(it, dict) and it.get("kind") == "question"]
    empty = not (resp.text or "").strip()
    return ReflectionResult(
        log_chars=len(log), model=model, lessons=lessons, questions=questions,
        empty_output=empty, raw=(resp.text or "")[:1500] if not items else "")


# ──────────────────────────────────────────────────────────────────────────────
# Persona `## Learned Lessons` block — parse / extract / render / merge
# ──────────────────────────────────────────────────────────────────────────────
# A lesson line carries a trailing HTML-comment trailer so the GC / refiner pass and
# dedup can read structured metadata without polluting the human-visible lesson text:
#     - <lesson text>  <!-- provisional conf=0.85 added=2026-06-17 -->
_LESSON_LINE = re.compile(r"^\s*-\s+(?P<text>.*?)\s*(?:<!--.*?-->)?\s*$")
_LESSON_TRAILER = re.compile(
    r"<!--\s*(?P<status>provisional|active)\s+conf=(?P<conf>[0-9.]+)\s+added=(?P<added>[\d-]+)\s*-->")


def split_lesson_block(content: str) -> Tuple[str, Optional[str], str]:
    """Split a persona record into (before, block_body, after) around the lessons header.

    block_body is None when the record has no `## Learned Lessons` section yet. The
    block_body excludes the header line itself and runs until the next `## ` heading
    (or end of document).
    """
    content = content or ""
    idx = content.find(LESSON_BLOCK_HEADER)
    if idx == -1:
        return content, None, ""
    before = content[:idx]
    rest = content[idx + len(LESSON_BLOCK_HEADER):]
    # Body runs until the next top-level "## " heading or EOF.
    m = re.search(r"\n##\s", rest)
    if m:
        body, after = rest[:m.start()], rest[m.start():]
    else:
        body, after = rest, ""
    return before, body, after


def extract_existing_lessons(content: str) -> List[str]:
    """Return the human-readable lesson texts already in the record (for dedup)."""
    _, body, _ = split_lesson_block(content)
    if body is None:
        return []
    out: List[str] = []
    for line in body.splitlines():
        if line.strip().startswith("<!--") or not line.strip().startswith("-"):
            continue
        m = _LESSON_LINE.match(line)
        if m and m.group("text").strip():
            out.append(m.group("text").strip())
    return out


def render_lesson_line(lesson: dict, *, added: str) -> str:
    """One terse, prompt-cheap line with a machine-readable trailer."""
    text = (lesson.get("lesson") or "").strip().rstrip(".")
    conf = float(lesson.get("confidence", 0.0) or 0.0)
    status = lesson.get("status", "provisional")
    return f"- {text}.  <!-- {status} conf={conf:.2f} added={added} -->"


def merge_lessons_into_content(content: str, new_lessons: List[dict], *,
                               added: Optional[str] = None) -> str:
    """APPEND-only merge of admitted lessons into the record's lessons block.

    Never rewrites existing lines — preserves hand edits and prior lessons. Creates the
    block at the end of the document if it doesn't exist yet.
    """
    added = added or time.strftime("%Y-%m-%d")
    new_lines = [render_lesson_line(l, added=added) for l in new_lessons]
    if not new_lines:
        return content

    before, body, after = split_lesson_block(content or "")
    if body is None:
        # Fresh block at end of document.
        sep = "" if (not content or content.endswith("\n\n")) else ("\n" if content.endswith("\n") else "\n\n")
        block = f"{sep}{LESSON_BLOCK_HEADER}\n{_LESSON_BLOCK_NOTE}\n" + "\n".join(new_lines) + "\n"
        return (content or "") + block

    # Append to the existing block, keeping its trailing whitespace tidy.
    body_trimmed = body.rstrip("\n")
    merged_body = body_trimmed + "\n" + "\n".join(new_lines) + "\n"
    rebuilt = before + LESSON_BLOCK_HEADER + merged_body
    if after:
        rebuilt = rebuilt.rstrip("\n") + "\n\n" + after.lstrip("\n")
    return rebuilt


# ──────────────────────────────────────────────────────────────────────────────
# Lesson GC (#640) — expire stale outages, decay-expire old provisional lessons,
# and collapse in-block near-duplicates. Read-only analysis + a separate apply
# step, same propose/apply split as the rest of this module.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LessonEntry:
    """One parsed line from a `## Learned Lessons` block."""
    text: str
    status: str            # "provisional" | "active" | "unmanaged" (no machine trailer)
    confidence: float
    added: Optional[str]   # YYYY-MM-DD, or None for hand-written/unmanaged lines
    line: str              # original raw line — used as the removal key


def parse_lesson_entries(content: str) -> List[LessonEntry]:
    """Parse a persona record's `## Learned Lessons` body into structured entries.

    Lines with no machine trailer (hand-authored, or written before the trailer
    format existed) get status="unmanaged" and added=None — the GC pass below never
    expires or auto-merges those; only auto-authored, dated lessons are eligible.
    """
    _, body, _ = split_lesson_block(content or "")
    if body is None:
        return []
    out: List[LessonEntry] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-") or stripped.startswith("<!--"):
            continue
        m = _LESSON_LINE.match(line)
        if not m or not m.group("text").strip():
            continue
        text = m.group("text").strip()
        trailer = _LESSON_TRAILER.search(line)
        if trailer:
            out.append(LessonEntry(
                text=text, status=trailer.group("status"),
                confidence=float(trailer.group("conf")), added=trailer.group("added"),
                line=line))
        else:
            out.append(LessonEntry(text=text, status="unmanaged", confidence=1.0,
                                    added=None, line=line))
    return out


@dataclass
class PruneReport:
    """Read-only GC findings for one persona record's lessons block."""
    persona_id: str = "lessons"
    outage: List[dict] = field(default_factory=list)      # transient-state lessons (#640)
    expired: List[dict] = field(default_factory=list)     # provisional, past TTL, never promoted
    duplicates: List[dict] = field(default_factory=list)  # near-dup of another line in the block
    kept_count: int = 0

    @property
    def to_remove(self) -> int:
        return len(self.outage) + len(self.expired) + len(self.duplicates)

    @property
    def remove_lines(self) -> set:
        return {d["line"] for d in (self.outage + self.expired + self.duplicates)}


# Same near-dup threshold as LessonGatekeeper's default — this pass is deliberately
# the same policy applied retroactively, not a stricter/looser second opinion.
DEFAULT_TTL_DAYS = 30
DEFAULT_DUP_THRESHOLD = 0.6


def analyze_lessons_for_prune(content: str, *, persona_id: str = "lessons",
                              ttl_days: int = DEFAULT_TTL_DAYS,
                              dup_threshold: float = DEFAULT_DUP_THRESHOLD,
                              today: Optional[str] = None) -> PruneReport:
    """Pure, read-only GC scan of an already-written lessons block (#640).

    Three findings, first-match-wins per line (an outage-shaped line is reported as
    outage even if it would also be past TTL):
      • outage    — transient failure/HTTP-status phrasing frozen as doctrine. Live
                    failure state is handled by src/core/tools/failure.py now, so a
                    lesson never needs to carry it (see lesson_gatekeeper.is_outage_shaped).
      • expired   — status=provisional, `added` older than ttl_days. The design
                    (docs/DESIGN_reflective-lessons.md) graduates surviving provisional
                    lessons to `active`; graduation isn't built yet, so an old
                    provisional line is either genuinely stale or stuck — either way
                    it shouldn't keep biasing every prompt with no review.
      • duplicate — near-duplicate (Jaccard over normalized tokens) of ANOTHER line in
                    the SAME block. Keeps the higher-confidence entry (ties broken by
                    the more recently-added one), flags the other.
    Lines with no machine trailer (hand-authored) are never flagged — `added` is None
    for those, and both the outage and expiry checks require a dated, auto-authored line.
    """
    import datetime as _dt
    from src.core.intelligence.lesson_gatekeeper import _norm_tokens, _jaccard, is_outage_shaped

    today_date = _dt.date.fromisoformat(today) if today else _dt.date.today()
    entries = parse_lesson_entries(content)
    report = PruneReport(persona_id=persona_id)
    flagged: set = set()

    for i, e in enumerate(entries):
        if e.added is None:
            continue  # unmanaged/hand-written — never auto-pruned
        if is_outage_shaped(e.text):
            report.outage.append({"lesson": e.text, "added": e.added, "line": e.line})
            flagged.add(i)
            continue
        if e.status == "provisional":
            age_days = (today_date - _dt.date.fromisoformat(e.added)).days
            if age_days > ttl_days:
                report.expired.append({
                    "lesson": e.text, "added": e.added, "age_days": age_days, "line": e.line})
                flagged.add(i)

    tokens = [_norm_tokens(e.text) for e in entries]
    for i in range(len(entries)):
        if i in flagged:
            continue
        for j in range(i + 1, len(entries)):
            if j in flagged:
                continue
            score = _jaccard(tokens[i], tokens[j])
            if score < dup_threshold:
                continue
            # Keep the higher-confidence entry; break ties on the more recent `added`.
            i_key = (entries[i].confidence, entries[i].added or "")
            j_key = (entries[j].confidence, entries[j].added or "")
            loser, winner = (j, i) if j_key <= i_key else (i, j)
            report.duplicates.append({
                "lesson": entries[loser].text, "added": entries[loser].added,
                "overlap": round(score, 2), "kept": entries[winner].text,
                "line": entries[loser].line})
            flagged.add(loser)

    report.kept_count = len(entries) - len(flagged)
    return report


def apply_prune(content: str, report: PruneReport) -> str:
    """Remove every line `analyze_lessons_for_prune` flagged. Pure string op — GATED
    WRITE happens in the caller (same propose/apply split as the rest of this module)."""
    remove_lines = report.remove_lines
    if not remove_lines:
        return content
    before, body, after = split_lesson_block(content or "")
    if body is None:
        return content
    kept = [ln for ln in body.splitlines() if ln not in remove_lines]
    new_body = "\n".join(kept)
    if not new_body.endswith("\n"):
        new_body += "\n"
    rebuilt = before + LESSON_BLOCK_HEADER + new_body
    if after:
        rebuilt = rebuilt.rstrip("\n") + "\n\n" + after.lstrip("\n")
    return rebuilt


def render_prune_report(report: PruneReport) -> str:
    out: List[str] = [f"# Reflective Lessons — GC / prune report (`{report.persona_id}`)\n"]
    out.append(f"- kept: {report.kept_count} | to remove: {report.to_remove} "
              f"(outage: {len(report.outage)}, expired: {len(report.expired)}, "
              f"duplicate: {len(report.duplicates)})\n")
    if report.outage:
        out.append("### Outage-shaped (transient state frozen as doctrine)\n")
        for d in report.outage:
            out.append(f"- `{d['added']}` {d['lesson']}")
        out.append("")
    if report.expired:
        out.append("### Expired (provisional, past TTL, never promoted)\n")
        for d in report.expired:
            out.append(f"- `{d['added']}` ({d['age_days']}d old) {d['lesson']}")
        out.append("")
    if report.duplicates:
        out.append("### Duplicates (near-dup of another line in this block)\n")
        for d in report.duplicates:
            out.append(f"- `{d['added']}` {d['lesson']}  _(overlap {d['overlap']} with kept: "
                       f"{d['kept']})_")
        out.append("")
    if not report.to_remove:
        out.append("Nothing to prune.")
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────────────────────
# Live tool catalog (for the gatekeeper's dead-tool guard)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_available_tools() -> List[str]:
    """Best-effort snapshot of live tool names from the cloud `tools` catalog.

    The gatekeeper's dead-tool guard skips itself when this is empty, so a fetch
    failure degrades to "don't reject on staleness" rather than blocking writes.
    """
    try:
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        db = get_supabase_manager()
        if not db.enabled:
            return []
        rows = db.query("tools", select="name", limit=2000)
        return [r["name"] for r in rows if r.get("name")]
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Propose (read-only) → Apply (gated write)
# ──────────────────────────────────────────────────────────────────────────────

# A candidate lesson whose max cosine similarity to an existing KNOWLEDGE-GRAPH node
# exceeds this is treated as already-known and dropped. The graph (config/
# personal_knowledge.md → data/knowledge_graph.json, via GraphRAGClient) is Chad's ONE
# live personal memory — the store actually injected into prompts. (The Supabase
# `memories` table is dormant per docs/architecture/MEMORY_AND_CONTEXT.md, so it is NOT
# the dedup target.) Threshold set from observed separation: a true dup (known relationship
# fact) scores ~0.77 against the graph; genuinely new facts score ~0.35.
GRAPH_DUP_THRESHOLD = 0.62


@dataclass
class LessonProposal:
    """Result of reflect → gatekeeper → graph-dedup. Read-only: describes WOULD-be writes."""
    reflection: ReflectionResult
    admitted: List[dict] = field(default_factory=list)
    rejected: List[dict] = field(default_factory=list)
    questions: List[dict] = field(default_factory=list)
    # Lessons dropped because the fact already lives in the knowledge graph.
    graph_duplicates: List[dict] = field(default_factory=list)
    graph_dedup_active: bool = False  # False if the graph/embeddings were unavailable
    # persona_id -> (current_content, proposed_content) — `tools` lessons
    diffs: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    # `user` lessons to ingest into the knowledge graph (one fact per dict)
    graph_writes: List[dict] = field(default_factory=list)
    available_tool_count: int = 0


async def _drop_graph_duplicates(admitted: List[dict]) -> Tuple[List[dict], List[dict], bool]:
    """Split gatekeeper-admitted lessons into (kept, graph_duplicates, dedup_active).

    Scores each lesson against Chad's knowledge graph (his ONE live personal memory). A
    node above GRAPH_DUP_THRESHOLD means the fact is already known, so writing it as a
    persona lesson would just duplicate it. Degrades safely: if the graph or its
    embeddings are unavailable, keep everything and report dedup_active=False.
    """
    from src.shared.logger import setup_logger
    log = setup_logger("Reflection")

    if not admitted:
        return [], [], False
    try:
        from src.core.intelligence.graph_rag_client import GraphRAGClient
        graph = GraphRAGClient()
        await graph.initialize()
    except Exception as e:
        log.warning(
            "graph-dedup DEGRADED — graph init failed (%s); admitted lessons NOT "
            "cross-checked against knowledge graph", e)
        return admitted, [], False

    kept: List[dict] = []
    dups: List[dict] = []
    active = False
    embed_failures = 0
    for lesson in admitted:
        text = (lesson.get("lesson") or "").strip()
        try:
            score, matched = await graph.max_similarity(text)
            active = True
        except Exception:
            embed_failures += 1
            kept.append(lesson)
            continue
        if score >= GRAPH_DUP_THRESHOLD:
            dups.append({**lesson, "graph_match": (matched or "")[:140],
                         "graph_score": round(score, 3)})
        else:
            kept.append(lesson)

    if not active and admitted:
        log.warning(
            "graph-dedup DEGRADED — embeddings unavailable for all %d admitted lesson(s); "
            "NOT cross-checked against knowledge graph", len(admitted))
    elif embed_failures:
        log.warning(
            "graph-dedup partial — %d/%d lesson(s) skipped embedding check",
            embed_failures, len(admitted))
    return kept, dups, active


async def propose_lessons(convos: int, model: str, *,
                          confidence_floor: float = 0.7,
                          max_per_cycle: int = 5) -> LessonProposal:
    """Reflect, gate, and compute the persona diff. WRITES NOTHING."""
    from src.core.intelligence.lesson_gatekeeper import LessonGatekeeper
    from src.infrastructure.repositories.memory_repository import MemoryRepository

    ctx = await load_already_known_context()
    refl = await reflect(convos, model, already_known_ctx=ctx)
    queue = ctx.get("queue_lessons") or []
    proposal = LessonProposal(
        reflection=refl,
        questions=filter_queue_duplicates(refl.questions, queue, field="question"),
        available_tool_count=len(ctx.get("tools") or []),
    )
    if refl.error:
        return proposal
    if not refl.lessons:
        return proposal

    repo = MemoryRepository()
    available = ctx.get("tools") or fetch_available_tools()

    # Persona record snapshots for diff rendering (gatekeeper corpus comes from ctx).
    current: Dict[str, str] = {}
    for pid in set(PERSONA_SCOPES.values()):
        current[pid] = repo.get_persona_config(pid) or ""

    existing_lessons = dedup_corpus_from_context(ctx)

    gate = LessonGatekeeper(
        confidence_floor=confidence_floor, max_per_cycle=max_per_cycle,
        available_tools=available, existing_lessons=existing_lessons)
    outcome = gate.filter(refl.lessons)
    proposal.rejected = outcome.rejected

    # Cross-store dedup: drop lessons whose fact already lives in the KNOWLEDGE GRAPH
    # (Chad's one live personal memory). The gatekeeper only deduped against the persona
    # `## Learned Lessons` block; this catches facts already known elsewhere.
    kept, graph_dups, dedup_active = await _drop_graph_duplicates(outcome.admitted)
    proposal.admitted = kept
    proposal.graph_duplicates = graph_dups
    proposal.graph_dedup_active = dedup_active
    if not kept:
        return proposal

    # Route by scope: `user` → knowledge graph; `tools` → persona record block.
    proposal.graph_writes = [l for l in kept if l.get("scope") in GRAPH_SCOPES]
    persona_lessons = [l for l in kept if l.get("scope") not in GRAPH_SCOPES]

    by_pid: Dict[str, List[dict]] = {}
    for lesson in persona_lessons:
        pid = PERSONA_SCOPES.get(lesson.get("scope"), "lessons")
        by_pid.setdefault(pid, []).append(lesson)
    for pid, lessons in by_pid.items():
        cur = current.get(pid, "")
        proposal.diffs[pid] = (cur, merge_lessons_into_content(cur, lessons))
    return proposal


async def apply_admitted(proposal: LessonProposal) -> Dict[str, bool]:
    """GATED WRITE. Persist admitted lessons to their destination stores.

    Only call after explicit human approval (see the CLI). Two destinations:
      • `tools` lessons → `update_persona_config` (INSERTS a new version, old retained —
        reversible — and carries the record's scope forward).
      • `user` lessons → `GraphRAGClient.ingest` into the knowledge graph (persisted to
        data/knowledge_graph.json; `forget()` can invalidate later — also reversible).
    """
    from src.shared.logger import setup_logger
    log = setup_logger("Reflection")
    results: Dict[str, bool] = {}

    if proposal.diffs:
        from src.infrastructure.repositories.memory_repository import MemoryRepository
        repo = MemoryRepository()
        for pid, (_cur, proposed) in proposal.diffs.items():
            try:
                ok = repo.update_persona_config(pid, proposed)  # scope carried forward
                results[f"persona:{pid}"] = bool(ok)
            except Exception as e:  # noqa: BLE001
                log.error(f"apply_admitted persona write failed for {pid}: {e}")
                results[f"persona:{pid}"] = False

    if proposal.graph_writes:
        try:
            from src.core.intelligence.graph_rag_client import GraphRAGClient
            graph = GraphRAGClient()
            await graph.initialize()
            for lesson in proposal.graph_writes:
                text = (lesson.get("lesson") or "").strip()
                try:
                    ok = await graph.ingest(text, source="reflection")
                    results[f"graph:{text[:40]}"] = bool(ok)
                except Exception as e:  # noqa: BLE001
                    log.error(f"apply_admitted graph ingest failed: {e}")
                    results[f"graph:{text[:40]}"] = False
        except Exception as e:  # noqa: BLE001
            log.error(f"apply_admitted graph init failed: {e}")
            for lesson in proposal.graph_writes:
                results[f"graph:{(lesson.get('lesson') or '')[:40]}"] = False
    return results


# ──────────────────────────────────────────────────────────────────────────────
# CLI — dry-run by default; --write requires a typed confirmation
# ──────────────────────────────────────────────────────────────────────────────

def _render_proposal(p: LessonProposal) -> str:
    out: List[str] = ["# Reflective Lessons — proposal (reflect → gatekeeper)\n"]
    r = p.reflection
    if r.error:
        out.append(f"⚠️ {r.error}")
        return "\n".join(out)
    out.append(f"- judge: `{r.model}` | log: {r.log_chars} chars | "
               f"live tools known: {p.available_tool_count}")
    out.append(f"- judge proposed: {len(r.lessons)} lessons, {len(r.questions)} questions")
    dedup_note = "" if p.graph_dedup_active else " ⚠️ graph-dedup DEGRADED (graph/embeddings unavailable)"
    out.append(f"- gatekeeper: {len(p.rejected)} rejected | already-in-graph dropped: "
               f"{len(p.graph_duplicates)} | **{len(p.admitted)} to write**{dedup_note}\n")

    if p.admitted:
        out.append("### ✅ To write (new knowledge — not in graph, tagged provisional)\n")
        out.append("| scope | conf | destination | lesson |")
        out.append("|---|---|---|---|")
        for l in p.admitted:
            dest = "knowledge graph" if l.get("scope") in GRAPH_SCOPES else "persona `lessons`"
            out.append(f"| `{l.get('scope')}` | {l.get('confidence')} | {dest} | {l.get('lesson')} |")
        out.append("")
    if p.graph_duplicates:
        out.append("### ♻️ Dropped — already in the knowledge graph\n")
        out.append("| scope | score | lesson | matched graph fact |")
        out.append("|---|---|---|---|")
        for l in p.graph_duplicates:
            out.append(f"| `{l.get('scope')}` | {l.get('graph_score')} | {str(l.get('lesson'))[:55]} | "
                       f"{str(l.get('graph_match'))[:70]} |")
        out.append("")
    if p.rejected:
        out.append("### ❌ Rejected\n")
        out.append("| scope | lesson | reason |")
        out.append("|---|---|---|")
        for l in p.rejected:
            out.append(f"| `{l.get('scope')}` | {str(l.get('lesson'))[:70]} | {l.get('reason')} |")
        out.append("")
    if p.questions:
        out.append("### ❓ Open questions for the user\n")
        for q in p.questions:
            out.append(f"- {q.get('question')}  _(why: {q.get('why')})_")
        out.append("")
    if p.diffs:
        out.append("### Proposed persona writes\n")
        for pid, (cur, new) in p.diffs.items():
            added = new[len(cur):] if new.startswith(cur) else new
            out.append(f"**`{pid}`** record — appended:\n```\n{added.strip()}\n```\n")
    if p.graph_writes:
        out.append("### Proposed knowledge-graph writes (`user` facts)\n")
        for l in p.graph_writes:
            out.append(f"- ingest: {l.get('lesson')}")
        out.append("")
    return "\n".join(out)


def _run_prune(*, ttl_days: int, do_write: bool) -> None:
    """CLI `--prune` mode (#640): GC an already-written `lessons` persona record.

    Dry-run by default (analysis + report only); `--write` requires the same typed
    'WRITE' confirmation as the reflect/apply path, since this mutates live persona
    content (reversible — persona_config is versioned).
    """
    from pathlib import Path
    from src.infrastructure.repositories.memory_repository import MemoryRepository

    persona_id = "lessons"
    repo = MemoryRepository()
    content = repo.get_persona_config(persona_id) or ""
    report = analyze_lessons_for_prune(content, persona_id=persona_id, ttl_days=ttl_days)

    rendered = render_prune_report(report)
    runs_dir = Path(__file__).resolve().parents[3] / "evals" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "reflect_prune_latest.md").write_text(rendered, encoding="utf-8")
    print("\n" + rendered)
    print("\n[reflect] report: evals/runs/reflect_prune_latest.md")

    if not report.to_remove:
        return
    if not do_write:
        print("\n[reflect] DRY-RUN — nothing pruned. Re-run with --prune --write to apply.")
        return

    print(f"\n[reflect] About to remove {report.to_remove} lesson(s) from `{persona_id}`.")
    try:
        answer = input("Type 'WRITE' to confirm: ").strip()
    except EOFError:
        answer = ""
    if answer != "WRITE":
        print("[reflect] aborted — no prune performed.")
        return

    pruned = apply_prune(content, report)
    ok = repo.update_persona_config(persona_id, pruned)
    print(f"[reflect] persona:{persona_id}: {'✅ pruned' if ok else '❌ FAILED'}")


def main():
    import argparse
    import asyncio
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser(
        description="Reflect over recent history, gate candidate lessons, and (with "
                    "--write) append admitted lessons to Chad's persona.")
    ap.add_argument("--convos", type=int, default=15)
    ap.add_argument("--model", default="qwen/qwen3.6-27b",
                    help="judge model id (lessons are high-stakes — use a capable loaded model)")
    ap.add_argument("--confidence-floor", type=float, default=0.7)
    ap.add_argument("--max-per-cycle", type=int, default=5)
    ap.add_argument("--write", action="store_true",
                    help="actually append admitted lessons (asks for typed confirmation)")
    ap.add_argument("--prune", action="store_true",
                    help="GC pass (#640): list expired/outage-shaped/duplicate lessons "
                        "already written to the `lessons` persona record, instead of "
                        "reflecting over new conversations. Combine with --write to apply.")
    ap.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS,
                    help="--prune only: age (days) after which an un-promoted provisional "
                        "lesson is flagged expired")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if args.prune:
        _run_prune(ttl_days=args.ttl_days, do_write=args.write)
        return

    print(f"[reflect] judge='{args.model}' over last {args.convos} conversations "
          f"({'WRITE-gated' if args.write else 'DRY-RUN'})…")
    proposal = asyncio.run(propose_lessons(
        args.convos, args.model,
        confidence_floor=args.confidence_floor, max_per_cycle=args.max_per_cycle))

    report = _render_proposal(proposal)
    runs_dir = Path(__file__).resolve().parents[3] / "evals" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "reflect_proposal_latest.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\n[reflect] report: evals/runs/reflect_proposal_latest.md")

    if not args.write:
        if proposal.admitted:
            print("\n[reflect] DRY-RUN — nothing written. Re-run with --write to apply.")
        return

    if not proposal.diffs and not proposal.graph_writes:
        print("\n[reflect] nothing admitted — nothing to write.")
        return

    # Typed confirmation gate. This mutates Chad's live behavior; make the human say so
    # explicitly. Both destinations are reversible (persona_config is versioned; graph
    # facts can be forgotten) — but we still require intent.
    dests = list(proposal.diffs) + (["knowledge-graph"] if proposal.graph_writes else [])
    print(f"\n[reflect] About to write {len(proposal.admitted)} lesson(s): "
          f"{len(proposal.diffs)} persona record(s) + {len(proposal.graph_writes)} graph "
          f"fact(s). Destinations: {', '.join(dests)}.")
    try:
        answer = input("Type 'WRITE' to confirm: ").strip()
    except EOFError:
        answer = ""
    if answer != "WRITE":
        print("[reflect] aborted — no write performed.")
        return

    results = asyncio.run(apply_admitted(proposal))
    for dest, ok in results.items():
        print(f"[reflect] {dest}: {'✅ written' if ok else '❌ FAILED'}")


if __name__ == "__main__":
    main()
