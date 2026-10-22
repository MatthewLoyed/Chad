"""
prompt_audit.py — Phase 3: audit the PROMPT ASSET itself (not per-query).

The `prompt_contract` judge (judges.py) runs per dataset case on whatever prompt that
case happened to assemble — noisy and redundant, since a scope's system prompt is mostly
static persona fragments. This lane audits the *asset directly*: it assembles each scope's
real system prompt ONCE (via the same SystemPromptBuilder the server uses), then runs:

  • DETERMINISTIC checks (zero LLM, the fast gate):
      - placeholder_leak        : an un-interpolated {placeholder} reached the model
      - disabled_delegate_leak  : a delegate_to_<disabled-agent> survived into the prompt
      - phantom_injection       : the prompt promises the harness injects a marker
                                  that no code path under src/ ever emits
      - doctrine_displaced_by_lessons : a reflection write has left a fragment with
                                  auto-authored lessons and no hand-written doctrine
      - duplicate_lines         : the same substantive line appears in 2+ fragments
      - near_duplicate_fragment : two fragments are ≥ JACCARD_DUP similar (one is dead weight)
      - token_bloat             : assembled system prompt exceeds the scope's token cap
                                  (ADVISORY — reported as a budget warning, never fails the lane)
  • SEMANTIC check (only with --judge): the prompt_contract rubric on the full per-scope
      prompt, catching contradictions deterministic checks can't (e.g. "delegate web search"
      vs "call brave_web_search directly").

This is the instrument that makes prompt CONSOLIDATION safe and provable: run it, fix the
flagged fragments in Supabase, re-run, watch the findings drop. Run via:

    python -m evals.runner --prompt-audit                # free deterministic gate
    python -m evals.runner --prompt-audit --judge --judge-model qwen/qwen3.5-9b
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from evals.core import Case, CaseResult

# Fragments more similar than this (token-set Jaccard) are flagged as near-duplicates.
JACCARD_DUP = 0.55
# A line must be at least this many chars (after normalization) to count for duplicate
# detection — short markdown headers ("### OUTPUT PROTOCOL:") legitimately repeat.
MIN_DUP_LINE_CHARS = 30
# Per-scope assembled-system-prompt token caps. Over cap = bloat finding. Generous headroom
# over today's sizes; tighten as you consolidate. Override any via PROMPT_AUDIT_CAP_<SCOPE>.
DEFAULT_TOKEN_CAPS = {
    "supervisor": 3500,
    "conversational": 2000,
    "heartbeat": 2200,
    "planner": 2200,
    "validator": 1600,
    "_subagent": 2000,   # applied to every specialist-agent scope
}

# Header the builder wraps every fragment in (prompt_builder.py). We split on it to recover
# per-fragment text (post-interpolation) without re-implementing the scope/ordering logic.
_FRAGMENT_RE = re.compile(
    r"### PERSONA COMPONENT: (?P<pid>.+?) \(Source: Cloud Master\)\n", re.MULTILINE
)
_LOCAL_FRAGMENT_RE = re.compile(r"^### (?P<pid>[a-z][a-z0-9_]*)\r?$", re.MULTILINE)
# A {placeholder} the builder is expected to resolve. Anything still matching post-build is a leak.
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _token_cap(scope: str, is_subagent: bool) -> int:
    env = os.getenv(f"PROMPT_AUDIT_CAP_{scope.upper()}")
    if env and env.isdigit():
        return int(env)
    if is_subagent:
        return DEFAULT_TOKEN_CAPS["_subagent"]
    return DEFAULT_TOKEN_CAPS.get(scope, 2500)


def _normalize_line(line: str) -> str:
    """Collapse whitespace + drop leading markdown/list markers for robust line comparison."""
    s = line.strip()
    s = re.sub(r"^[#>\-\*\d\.\)\s]+", "", s)  # strip "###", "- ", "1. ", "> " prefixes
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _tokenize(text: str) -> set:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _split_fragments(assembled: str, fragment_ids: Optional[set[str]] = None) -> List[Tuple[str, str]]:
    """Recover [(pid, fragment_text), ...] from the builder's assembled output."""
    parts: List[Tuple[str, str]] = []
    matches = list(_FRAGMENT_RE.finditer(assembled))
    # Local-file headers are deliberately terse. Only registered fragment IDs
    # delimit blocks; ordinary Markdown headings inside a prompt do not.
    matches.extend(m for m in _LOCAL_FRAGMENT_RE.finditer(assembled)
                   if fragment_ids is not None and m.group('pid') in fragment_ids)
    matches.sort(key=lambda match: match.start())
    for i, m in enumerate(matches):
        pid = m.group("pid").strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(assembled)
        parts.append((pid, assembled[start:end].strip()))
    return parts


# ── Deterministic detectors ───────────────────────────────────────────────────


def _check_placeholder_leak(assembled: str) -> List[str]:
    leaks = sorted(set(_PLACEHOLDER_RE.findall(assembled)))
    return [f"placeholder_leak: '{{{p}}}' was never interpolated — the model sees the literal "
            f"placeholder (builder resolves a different name, or the key is unknown)"
            for p in leaks]


def _check_disabled_delegate_leak(assembled: str) -> List[str]:
    try:
        from src.core.orchestration.agents.factory import AGENT_CONFIGS
        from src.shared.config_manager import ConfigManager
        disabled = set(ConfigManager.get("disabled_subagents") or [])
    except Exception:
        return []
    fails = []
    for agent in disabled:
        token = f"delegate_to_{agent}"
        if token in assembled:
            fails.append(f"disabled_delegate_leak: '{token}' appears in the prompt but "
                         f"'{agent}' is disabled — the model is told to use an agent that doesn't exist")
    return fails


# Prompt text claiming the harness will inject a specific literal marker, e.g.
# "System injects [COMPLETED SO FAR]/[NEXT ACTION]". If nothing emits that marker,
# the model is told to wait for a signal that never arrives.
# Case-insensitivity is scoped to the prose prefix only: the MARKER itself must stay
# strictly upper-case, or every bracketed lowercase word in a prompt becomes a hit.
_PROMISED_MARKER_RE = re.compile(
    r"(?i:(?:system|harness)\s+(?:will\s+)?inject(?:s|ed)?)\b[^.\n]*?(\[[A-Z][A-Z0-9 _/]{3,}\])"
)


def _check_phantom_injection(assembled: str) -> List[str]:
    """A prompt promising an injected marker that no code path produces.

    Real case this catches: `tool_calling.md` told the supervisor "System injects
    [COMPLETED SO FAR]/[NEXT ACTION] — prioritize NEXT ACTION" for months. Nothing
    ever emitted either marker. A small model takes that literally and waits on a
    signal that cannot arrive. Deterministic checks could not see it because the
    prompt is internally consistent — the contradiction is with the CODE.
    """
    markers = sorted(set(_PROMISED_MARKER_RE.findall(assembled)))
    if not markers:
        return []

    src = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
    )
    emitted: set = set()
    for root, _dirs, files in os.walk(src):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            try:
                with open(os.path.join(root, fname), "r", encoding="utf-8", errors="ignore") as fh:
                    body = fh.read()
            except OSError:
                continue
            for marker in markers:
                if marker in body:
                    emitted.add(marker)

    return [
        f"phantom_injection: the prompt promises the harness injects '{m}', but no "
        f"file under src/ ever emits it — the model is told to wait for a signal "
        f"that never arrives"
        for m in markers
        if m not in emitted
    ]


# A fragment whose entire body is the auto-authored lessons block has lost whatever
# hand-written doctrine used to live in it.
_LESSON_BLOCK_HEADER = "## Learned Lessons"


def _check_doctrine_displaced_by_lessons(fragments: List[Tuple[str, str]]) -> List[str]:
    """A fragment that is now ONLY auto-authored lessons, with no doctrine left.

    Real case this catches, twice over: the reflection loop appended lessons into
    `tools.md`, and because the overlay overrides baseline WHOLE-FILE, the live
    persona ended up with a lessons list and NO tool-calling doctrine at all —
    silently, for weeks, in every prompt (`scope: always`). reflection.py was fixed
    to write to its own `lessons` fragment, but the already-damaged overlay file
    sat there unnoticed until this audit. Nothing detected it because the prompt
    is internally consistent; what's wrong is what ISN'T there.

    Exempts the `lessons` fragment itself, which is supposed to be lessons-only.
    """
    findings = []
    for pid, text in fragments:
        if pid == "lessons" or _LESSON_BLOCK_HEADER not in text:
            continue
        head, _, _ = text.partition(_LESSON_BLOCK_HEADER)
        # Strip HTML comments and frontmatter noise before judging "is anything left?"
        prose = re.sub(r"<!--.*?-->", "", head, flags=re.DOTALL)
        prose = re.sub(r"^---.*?---", "", prose, flags=re.DOTALL).strip()
        if len(prose) < 80:
            findings.append(
                f"doctrine_displaced_by_lessons: fragment '{pid}' now contains the "
                f"auto-authored '{_LESSON_BLOCK_HEADER}' block and almost no doctrine "
                f"({len(prose)} chars before it) — a reflection write has displaced "
                f"hand-written guidance. Lessons belong in the 'lessons' fragment."
            )
    return findings


def _check_duplicate_lines(fragments: List[Tuple[str, str]]) -> List[str]:
    """A substantive line living in 2+ fragments is redundant context (and a consolidation target)."""
    line_owners: Dict[str, set] = {}
    for pid, text in fragments:
        seen_in_frag = set()
        for raw in text.splitlines():
            norm = _normalize_line(raw)
            if len(norm) < MIN_DUP_LINE_CHARS or norm in seen_in_frag:
                continue
            seen_in_frag.add(norm)
            line_owners.setdefault(norm, set()).add(pid)
    fails = []
    for norm, owners in line_owners.items():
        if len(owners) >= 2:
            fails.append(f"duplicate_line: identical line in {sorted(owners)} — \"{norm[:70]}…\"")
    return fails


# Stale claims that contradict docs/architecture/MEMORY_AND_CONTEXT.md (personal facts = graph only).
_STALE_PERSONAL_MEMORY_MARKERS = (
    "memory is centralized in supabase",
    "### relevant recalled memories",
    "relevant recalled memories",
)


# Scopes that actually do personal-fact CRUD and therefore must carry `memory_policy`.
# Worker scopes (planner, validator, scout, subagents) never write memory — the builder
# intentionally strips the policy from them (prompt_builder.WORKER_EXCLUDED_PERSONA), so
# its absence there is correct, not a finding.
_MEMORY_POLICY_REQUIRED_SCOPES = {"supervisor", "conversational", "heartbeat", "voice"}


def _check_memory_architecture(assembled: str, fragments: List[Tuple[str, str]],
                               scope: str) -> List[str]:
    """Ensure persona prompts describe the knowledge graph, not dormant Supabase flat facts."""
    fails: List[str] = []
    lower = assembled.lower()
    for marker in _STALE_PERSONAL_MEMORY_MARKERS:
        if marker in lower:
            fails.append(
                f"stale_memory_claim: prompt still references '{marker}' — personal facts live in "
                f"the knowledge graph (`### PERSONAL CONTEXT`), not Supabase flat-fact memory"
            )
            break
    frag_ids = {pid for pid, _ in fragments}
    if scope in _MEMORY_POLICY_REQUIRED_SCOPES and "memory_policy" not in frag_ids:
        fails.append(
            "missing_memory_policy: no `memory_policy` persona fragment in a user-facing scope — "
            "restore prompts/persona/memory_policy.md or its approved local override"
        )
    return fails


def _check_near_duplicate_fragments(fragments: List[Tuple[str, str]]) -> List[str]:
    fails = []
    toks = [(pid, _tokenize(text)) for pid, text in fragments if text]
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            (a, ta), (b, tb) = toks[i], toks[j]
            if not ta or not tb:
                continue
            jac = len(ta & tb) / len(ta | tb)
            if jac >= JACCARD_DUP:
                fails.append(f"near_duplicate_fragment: '{a}' and '{b}' are {jac:.0%} similar — "
                             f"merge into one source of truth and delete the other")
    return fails


# ── Per-scope assembly ─────────────────────────────────────────────────────────


def _audited_scopes() -> List[str]:
    scopes = ["supervisor", "conversational", "heartbeat", "planner", "validator"]
    try:
        from src.core.orchestration.agents.factory import AGENT_CONFIGS
        from src.shared.config_manager import ConfigManager
        disabled = set(ConfigManager.get("disabled_subagents") or [])
        scopes += [a for a in AGENT_CONFIGS.keys() if a not in disabled]
    except Exception:
        pass
    return scopes


def _audit_one(scope: str, is_subagent: bool, prompts_offline: bool = False) -> CaseResult:
    from src.core.intelligence.prompt_builder import SystemPromptBuilder
    from src.core.utils.token_counter import TokenCounter

    case_id = f"prompt_audit::{scope}"
    try:
        assembled = SystemPromptBuilder._read_agent_files(scope=scope)  # noqa: SLF001
    except Exception as e:
        return CaseResult(case_id, f"audit scope '{scope}'", passed=False,
                          error=f"{type(e).__name__}: {e}", lane="prompt-audit")

    from src.infrastructure.repositories.prompt_registry import PromptRegistry
    fragments = _split_fragments(assembled, set(PromptRegistry.get_instance()._prompts))
    tokens = TokenCounter.count_text(assembled) if assembled else 0
    cap = _token_cap(scope, is_subagent)
    # Per-fragment token breakdown — the compaction targeting signal (which fragment is heaviest).
    frag_tokens = {pid: TokenCounter.count_text(text) for pid, text in fragments if text}

    fails: List[str] = []
    fails += _check_placeholder_leak(assembled)
    fails += _check_disabled_delegate_leak(assembled)
    fails += _check_phantom_injection(assembled)
    fails += _check_doctrine_displaced_by_lessons(fragments)
    fails += _check_duplicate_lines(fragments)
    fails += _check_near_duplicate_fragments(fragments)
    fails += _check_memory_architecture(assembled, fragments, scope)

    # Token bloat is ADVISORY, not gating. The caps are judgement calls, not correctness
    # boundaries — a prompt 40 tokens over is not broken, and failing the lane on it trains
    # you to raise the cap rather than read the number. What's actually wanted here is
    # awareness of context growth over time, so this rides in `budget_warnings` (surfaced
    # under "BUDGET NOTES" and trended in history.jsonl) alongside the per-fragment
    # breakdown that tells you WHICH fragment grew. Contradictions, placeholder leaks and
    # duplicate lines stay hard failures — those are defects regardless of size.
    budget: List[str] = []
    if tokens > cap:
        budget.append(f"token_bloat: assembled prompt is {tokens} tokens > cap {cap} "
                      f"(heaviest: {', '.join(f'{p} {t}tok' for p, t in sorted(frag_tokens.items(), key=lambda kv: -kv[1])[:3])})")

    result = CaseResult(
        case_id, f"audit scope '{scope}'", passed=not fails,
        assertion_failures=fails, budget_warnings=budget, lane="prompt-audit",
        metrics={"scope": scope, "fragments": [p for p, _ in fragments],
                 "fragment_count": len(fragments), "system_tokens": tokens, "token_cap": cap,
                 "fragment_tokens": frag_tokens, "prompts_offline": prompts_offline},
    )
    result.transcript = {"system_prompt": assembled, "tool_names": [],
                         "system_tokens": tokens, "total_tokens": tokens}
    return result


# ── Entry point used by the runner ─────────────────────────────────────────────


async def run_prompt_audit(do_judge: bool, judge_model: str, quiet: bool = True
                           ) -> Tuple[List[Case], List[CaseResult]]:
    """Assemble every scope's real prompt, audit it, and (optionally) judge for contradictions."""
    os.environ.setdefault("CHAD_TESTING", "true")
    from src.shared.config_manager import ConfigManager
    from src.infrastructure.repositories.prompt_registry import PromptRegistry
    ConfigManager.load_config()

    prompts_offline = False
    try:
        await PromptRegistry.get_instance().warmup()
        source = "Local persona files (baseline + optional private overlay)"
    except Exception as e:
        case = Case(id='prompt_audit::load', prompt='load local prompt assets', category='prompt-audit')
        result = CaseResult(case.id, case.prompt, passed=False,
                            error=f'Local persona load failed: {type(e).__name__}: {e}', lane='prompt-audit')
        return [case], [result]
    print(f"[prompt_audit] prompt source: {source}")

    try:
        from src.core.orchestration.agents.factory import AGENT_CONFIGS
        agent_ids = set(AGENT_CONFIGS.keys())
    except Exception:
        agent_ids = set()

    scopes = _audited_scopes()
    cases: List[Case] = []
    results: List[CaseResult] = []

    judge_adapter = None
    if do_judge:
        from evals.judges import build_judge_adapter
        judge_adapter = build_judge_adapter(judge_model)

    from evals import judges
    for i, scope in enumerate(scopes, 1):
        is_subagent = scope in agent_ids
        result = _audit_one(scope, is_subagent, prompts_offline=prompts_offline)
        if do_judge and judge_adapter is not None and not result.error:
            try:
                verdict = await judges.prompt_contract(
                    judge_adapter, prompt=f"(scope audit: {scope})",
                    system_prompt=result.transcript.get("system_prompt", ""))
                result.judge_results = [verdict]
            except Exception as e:
                result.judge_results = [{"rubric": "prompt_contract", "passed": False,
                                         "score": 0.0, "reasons": [f"judge error: {e}"]}]
        cases.append(Case(id=result.case_id, prompt=result.prompt, category="prompt-audit"))
        results.append(result)
        m = result.metrics
        print(f"  [{i}/{len(scopes)}] {scope}: {'PASS' if result.overall_passed else 'FAIL'} "
              f"({m.get('fragment_count')} frags, {m.get('system_tokens')} tok, "
              f"{len(result.assertion_failures)} findings)")

    # Compaction targeting: heaviest UNIQUE fragments across all scopes (dedup by id, since
    # 'always' fragments like identity/soul/user appear in every scope).
    uniq: Dict[str, int] = {}
    for r in results:
        for pid, tok in (r.metrics.get("fragment_tokens") or {}).items():
            uniq[pid] = max(uniq.get(pid, 0), tok)
    if uniq:
        print("\n[prompt_audit] heaviest fragments (compaction targets):")
        for pid, tok in sorted(uniq.items(), key=lambda kv: kv[1], reverse=True)[:12]:
            print(f"    {pid:26} {tok:>5} tok")
    return cases, results
