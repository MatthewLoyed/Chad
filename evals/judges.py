"""
judges.py — LLM-as-judge rubrics (Phase 2).

Catches the *semantic* failures deterministic asserts can't: wrong tool choice,
an unreachable subagent, incoherent plans, contradictory prompt instructions.

Cost model:
  • Default judge = cloud active model (Gemini) — one short call per (case, rubric).
  • `--judge local` routes to the LM Studio model (Gemma/Qwen 3.6) for free looping.
  • Judges run only on cases that opt in via the case's "judge": [...] list.
  • Several rubrics (tool_appropriateness, prompt_contract) judge the *dry-run payload*,
    so they need ZERO harness inference — only the cheap judge call.

Each rubric returns {"rubric", "passed": bool, "score": 0..10, "reasons": [..]}.
"""

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

PASS_THRESHOLD = 6.0  # score out of 10; below this = fail (was 0.6 on the old 0–1 scale)

# Heavy workflow lanes can produce large tool-result blobs. Budget judge input so a
# small-context LM Studio load (8k–12k) still gets a verdict. Override via env:
#   EVAL_JUDGE_NUM_CTX          — request larger ctx from LM Studio (if supported)
#   EVAL_JUDGE_USER_CHAR_BUDGET — hard cap on the user message chars
#   EVAL_JUDGE_TIMEOUT_S        — per-rubric wall clock (default 20m; full trajectories are slow)
JUDGE_NUM_CTX = int(os.getenv("EVAL_JUDGE_NUM_CTX", "32768"))
# 0 = no char cap on the judge user message (HITL quality: full tool bodies / trajectory).
JUDGE_USER_CHAR_BUDGET = int(os.getenv("EVAL_JUDGE_USER_CHAR_BUDGET", "0"))
JUDGE_TIMEOUT_S = int(os.getenv("EVAL_JUDGE_TIMEOUT_S", "1200"))
JUDGE_MAX_OUTPUT_TOKENS = int(os.getenv("EVAL_JUDGE_MAX_OUTPUT_TOKENS", "2048"))
JUDGE_THINKING_LEVEL = os.getenv("EVAL_JUDGE_THINKING_LEVEL", "high")
# Quality rubrics (planner waste, groundedness) are open-ended — temp 0 often locks
# into a green path and skips inefficiencies. Override via EVAL_JUDGE_TEMPERATURE.
JUDGE_TEMPERATURE = float(os.getenv("EVAL_JUDGE_TEMPERATURE", "0.8"))


# ── Adapter construction (mirrors src/application/system_factory.py) ─────────


def build_judge_adapter(mode: str = "local"):
    """
    mode:
      "local"  → config's local_models.active_model on LM Studio
      "cloud"  → config's cloud active_model
      <id>     → explicit model id on LM Studio (e.g. "qwen/qwen3.5-9b") — RECOMMENDED:
                 pin a small loaded model so the judge never JIT-loads a heavy one.
    """
    from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
    from src.shared.config_manager import ConfigManager
    ConfigManager.load_config()

    if mode == "local":
        cfg = ConfigManager.get_config() if hasattr(ConfigManager, "get_config") else {}
        local = (cfg or {}).get("local_models", {})
        adapter = LiteLLMAdapter(model_name=local.get("active_model") or "local-model",
                                 base_url=local.get("base_url") or "http://localhost:1234/v1",
                                 api_key=local.get("api_key") or "lm-studio")
    elif mode == "cloud":
        model = ConfigManager.get_active_model()
        api_key = os.getenv("GEMINI_API_KEY") if "gemini" in (model or "").lower() else None
        adapter = LiteLLMAdapter(model_name=model, api_key=api_key)
    else:
        # Explicit model id → LM Studio endpoint.
        adapter = LiteLLMAdapter(model_name=mode, base_url="http://localhost:1234/v1", api_key="lm-studio")
    adapter._eval_judge_num_ctx = JUDGE_NUM_CTX  # noqa: SLF001 — eval-only hint for generate()
    # Tag judge inference "background" so lmstudio-gate (issue #478) queues it behind any
    # real interactive/daemon turn sharing the one LM Studio slot instead of contending.
    adapter._chad_priority = "background"  # noqa: SLF001
    return adapter


# ── Judge call + structured verdict (tool-first, JSON fallback) ───────────────

_SUBMIT_VERDICT_TOOL: Dict[str, Any] = {
    "name": "submit_verdict",
    "description": "Submit your judge verdict.",
    "parameters": {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "description": (
                    "0–10 quality score (integers preferred). Start at 10 for a correct, "
                    "efficient trajectory; dock points for each inefficiency."
                ),
            },
            "pass": {
                "type": "boolean",
                "description": f"True if score >= {PASS_THRESHOLD:g} (pass threshold out of 10)",
            },
            "reasons": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short English bullets explaining the score",
            },
            "inefficiencies": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Waste or process bugs: rediscovery after gather_brief, duplicate "
                    "file reads, title-as-file_id, double Scout, under-tooled website plans, etc. "
                    "Each item MUST reduce score below 10."
                ),
            },
            "suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional short notes only. Prefer naming a failure class "
                    "(rediscovery_after_brief, title_as_file_id, duplicate_same_id_reads, "
                    "plan_repeats_gather). Do NOT invent prompt/code patches — harness "
                    "fixes belong to the Chad-on-Chad loop, not this judge."
                ),
            },
        },
        "required": ["score", "pass", "reasons"],
    },
}

_VERDICT_TOOL_CHOICE = "required"

_VERDICT_INSTRUCTION = (
    "Think in English about the rubric criteria AND look for inefficiencies "
    "(duplicate work, rediscovery after confirmed facts, bad file_ids, missing "
    "build tools for website goals). When ready, call `submit_verdict` with "
    "score, pass, reasons, inefficiencies, and suggestions. "
    "Do not write JSON in your message — use the tool.\n"
    "SCORING (0–10, not 0–1):\n"
    "  • Start at 10 for a correct, efficient trajectory that meets the rubric.\n"
    "  • Dock for each clear inefficiency, e.g. minor extra same-id read −1..−2; "
    "double Scout / rediscovery after gather_brief −2..−3; missing required tool "
    "for the goal (e.g. no code agent for a website) −3..−5; wrong/broken outcome ≤4.\n"
    "  • Every item listed under inefficiencies must leave score < 10.\n"
    f"  • pass=true only when score >= {PASS_THRESHOLD:g}.\n"
    "ANALYSIS ONLY: name failure classes in inefficiencies/suggestions. Do not propose "
    "prompt or code patches — another loop owns harness fixes."
)


def _verdict_from_tool_calls(tool_calls: Optional[List[Any]]) -> Optional[Dict[str, Any]]:
    """Pull score/pass/reasons from a submit_verdict tool call, if present."""
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        if tc.get("name") != "submit_verdict":
            continue
        args = tc.get("args")
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            parsed = _extract_json_object(args)
            if isinstance(parsed, dict):
                return parsed
    return None


def _coerce_score_0_10(raw: Any) -> float:
    """Normalize judge score to 0–10.

    Legacy verdicts used 0–1 fractions (0.8, 1.0). New scale is 0–10. Values in
    (0, 1] are treated as fractions and scaled ×10; values >1 are clamped to 10.
    """
    try:
        s = float(raw or 0.0)
    except (TypeError, ValueError):
        s = 0.0
    if 0.0 < s <= 1.0:
        s *= 10.0
    return round(min(10.0, max(0.0, s)), 2)


def _dock_for_inefficiencies(score: float, inefficiencies: Any) -> float:
    """Harness floor: each listed inefficiency costs at least 1 point from 10."""
    if isinstance(inefficiencies, str):
        items = [inefficiencies] if inefficiencies.strip() else []
    else:
        items = [x for x in (inefficiencies or []) if str(x).strip()]
    if not items:
        return score
    return min(score, 10.0 - min(len(items), 5))


def _truncate(text: str, max_chars: int, label: str) -> str:
    if not text:
        return ""
    if max_chars is None or max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}\n... [{label}: {omitted} chars truncated for judge context]"


_YAML_FENCE = re.compile(r"```yaml\n.*?```", re.DOTALL | re.IGNORECASE)
_JSON_BLOBISH = re.compile(r"\{[^{}]{2500,}\}")


def coalesce_tool_results_for_judge(text: str, *, max_chars: Optional[int] = None) -> str:
    """Shrink browser/Drive dumps so the judge fits LM Studio ctx.

    Unlimited ``EVAL_JUDGE_USER_CHAR_BUDGET=0`` still blew Gemma with ~64k chars of
    Playwright YAML (amazon_aura VOID / mid-judge stall, church_2h + loop_2h).
    Collapse fences first, then apply a soft cap derived from ``JUDGE_NUM_CTX``.
    """
    if not text:
        return ""
    collapsed = _YAML_FENCE.sub(
        lambda m: f"[yaml snapshot collapsed: {m.group(0).count(chr(10))} lines]",
        text,
    )

    # Oversized single-line JSON payloads (list dumps) — keep a head sample.
    def _json_sub(m: re.Match) -> str:
        body = m.group(0)
        return body[:600] + f"... [json {len(body) - 600} chars collapsed]"

    collapsed = _JSON_BLOBISH.sub(_json_sub, collapsed)
    lines: List[str] = []
    for line in collapsed.splitlines():
        if len(line) > 1800:
            lines.append(line[:700] + f"... [{len(line) - 700} chars collapsed]")
        else:
            lines.append(line)
    collapsed = "\n".join(lines)
    if max_chars is None:
        if JUDGE_USER_CHAR_BUDGET > 0:
            max_chars = min(12000, JUDGE_USER_CHAR_BUDGET)
        else:
            # Soft ceiling: leave room for system + prompt + response inside num_ctx.
            max_chars = max(8000, min(14000, (JUDGE_NUM_CTX * 3) // 2))
    return _truncate(collapsed, max_chars, "tool_results")


def _budget_workflow_text(*, prompt: str, plan_txt: str = "", tools: str = "",
                          tool_results: str = "", response: str = "") -> tuple:
    """Allocate char budget across workflow artifacts (tool_results is usually largest).

    ``EVAL_JUDGE_USER_CHAR_BUDGET=0`` still runs ``coalesce_tool_results_for_judge`` so
    browser YAML dumps cannot VOID a local judge (amazon_aura / research compare).
    """
    budget = JUDGE_USER_CHAR_BUDGET
    if budget is None or budget <= 0:
        # Still coalesce oversized dumps (browser YAML) so local judges don't VOID.
        return prompt, plan_txt, tools, coalesce_tool_results_for_judge(tool_results or ""), response
    prompt = _truncate(prompt, min(800, budget // 6), "prompt")
    plan_txt = _truncate(plan_txt, min(2500, budget // 4), "plan")
    tools = _truncate(tools, min(1200, budget // 8), "tools")
    response = _truncate(response, min(4000, budget // 3), "response")
    # Remainder goes to tool_results (often the bulk of a heavy workflow).
    used = len(prompt) + len(plan_txt) + len(tools) + len(response) + 200
    tool_cap = max(1500, budget - used)
    tool_results = coalesce_tool_results_for_judge(tool_results or "(none)", max_chars=tool_cap)
    return prompt, plan_txt, tools, tool_results, response


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON object extraction from judge model output."""
    text = (text or "").strip()
    if not text:
        return None
    # Strip common Qwen/Gemma thinking wrappers before parsing.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    try:
        from json_repair import repair_json
        data = repair_json(text, return_objects=True)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Last resort: grab the first {...} block.
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json
                data = repair_json(m.group(0), return_objects=True)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return None


def _judge_failure_diag(*, user: str, kind: str, snippet: str = "",
                        num_ctx: Optional[int] = None) -> Dict[str, Any]:
    """Explain *why* the judge declined so HITL can rejudge with a fix (cap / coalesce / ctx)."""
    user_chars = len(user or "")
    est_tokens = user_chars // 4  # rough English chars/token
    ctx = int(num_ctx or JUDGE_NUM_CTX or 0)
    # Leave headroom for system prompt + output. Only blame context when estimated
    # input would actually exceed num_ctx — never a fixed char cutoff (52k chars ≈ 13k
    # tok still fits cleanly in 32k).
    headroom = max(512, ctx // 8) if ctx else 2048
    likely = kind
    if kind in ("empty", "unparseable"):
        if ctx and est_tokens + headroom >= ctx:
            likely = "context_too_large"
        else:
            likely = "empty_or_unparseable_output"
    elif kind == "timeout":
        likely = "judge_timeout"
    diag: Dict[str, Any] = {
        "kind": kind,
        "likely_cause": likely,
        "user_chars": user_chars,
        "est_input_tokens": est_tokens,
        "num_ctx": ctx or None,
    }
    if snippet:
        diag["snippet"] = snippet[:200]
    return diag


def _needs_rejudge_verdict(*, user: str, kind: str, detail: str,
                           num_ctx: Optional[int] = None,
                           snippet: str = "") -> Dict[str, Any]:
    """Inconclusive judge call: not a Brain fail, not a green pass — needs rejudge."""
    diag = _judge_failure_diag(user=user, kind=kind, snippet=snippet, num_ctx=num_ctx)
    reasons = [
        f"needs_rejudge: {detail}",
        (f"diag: user_chars={diag['user_chars']} (~{diag['est_input_tokens']} tok) "
         f"num_ctx={diag['num_ctx']} likely_cause={diag['likely_cause']}"),
    ]
    return {
        "score": None,
        "pass": False,
        "needs_rejudge": True,
        "diag": diag,
        "reasons": reasons,
    }


async def _ask(adapter, system: str, user: str,
               temperature: Optional[float] = None) -> Dict[str, Any]:
    if temperature is None:
        temperature = JUDGE_TEMPERATURE
    if JUDGE_USER_CHAR_BUDGET > 0 and len(user) > JUDGE_USER_CHAR_BUDGET:
        user = _truncate(user, JUDGE_USER_CHAR_BUDGET, "user_message")
    messages = [
        {"role": "system", "content": f"{system}\n\n{_VERDICT_INSTRUCTION}"},
        {"role": "user", "content": user},
    ]
    extra_body: Dict[str, Any] = {}
    num_ctx = getattr(adapter, "_eval_judge_num_ctx", None) or JUDGE_NUM_CTX
    if num_ctx:
        # LM Studio / llama.cpp may honour per-request ctx when the server allows it.
        extra_body["num_ctx"] = num_ctx
        extra_body.setdefault("options", {})["num_ctx"] = num_ctx
    data = None
    last_kind = "empty"
    last_snippet = ""
    for attempt in range(1, 4):
        try:
            resp = await asyncio.wait_for(
                adapter.generate(
                    messages=messages,
                    tools=[_SUBMIT_VERDICT_TOOL],
                    tool_choice=_VERDICT_TOOL_CHOICE,
                    temperature=temperature,
                    thinking_level=JUDGE_THINKING_LEVEL,
                    max_tokens=JUDGE_MAX_OUTPUT_TOKENS,
                    extra_litellm_body=extra_body or None,
                ),
                timeout=JUDGE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            if attempt == 3:
                return _needs_rejudge_verdict(
                    user=user, kind="timeout", num_ctx=num_ctx,
                    detail=(f"judge timed out after 3 attempts (each {JUDGE_TIMEOUT_S}s)"),
                )
            await asyncio.sleep(1.0)
            continue

        text = (resp.text or "").strip()
        data = _verdict_from_tool_calls(getattr(resp, "tool_calls", None))
        if data is None:
            # Fallback for cloud / non-tool models that still emit JSON in content.
            data = _extract_json_object(text)
        if data is not None:
            break

        last_snippet = text[:200] if text else "(empty response / no submit_verdict)"
        last_kind = "empty" if not text else "unparseable"
        if attempt < 3:
            await asyncio.sleep(1.5)

    if data is None:
        # After 3 attempts, report that the judge could not properly evaluate the turn.
        return _needs_rejudge_verdict(
            user=user, kind=last_kind, num_ctx=num_ctx, snippet=last_snippet,
            detail=f"judge returned no parseable verdict after 3 attempts: {last_snippet!r}",
        )
    score = _coerce_score_0_10(data.get("score", 0.0))
    data = _normalize_verdict_lists(dict(data))
    inefficiencies = data.get("inefficiencies") or []
    score = _dock_for_inefficiencies(score, inefficiencies)
    # Model may claim pass=true at 10 with inefficiencies listed; after docking, enforce threshold.
    if "pass" in data:
        passed = bool(data["pass"]) and score >= PASS_THRESHOLD
    else:
        passed = score >= PASS_THRESHOLD
    reasons = data.get("reasons") or []
    return {
        "score": score,
        "pass": passed,
        "reasons": reasons,
        "inefficiencies": inefficiencies,
        "suggestions": data.get("suggestions") or [],
    }


# EVAL_JUDGE_SAMPLES — how many independent judge calls _ask_or_majority makes per verdict.
# Default 1 = today's single-call behavior, byte-for-byte unchanged (backward compatible, opt-in).
# 2026 practice for judge bias control (position/length/self-preference): sample N times and take
# a majority vote instead of trusting one roll. Only wired into the rubrics that gate pass/fail on
# the most CI-critical suites (tool_appropriateness, workflow_outcome) — not every rubric, since
# that would multiply judge cost/latency 3x indiscriminately for no proven benefit on rubrics that
# already agree well with humans (see evals/golden/judge_golden.jsonl calibration, issue #312:
# prompt_contract/response_grounded/workflow_outcome were already 100% single-sample agreement).
JUDGE_SAMPLES = int(os.getenv("EVAL_JUDGE_SAMPLES", "1"))
# temp 0.0 is near-deterministic on the small local models this harness targets — repeating
# a temp-0.0 call mostly returns the same verdict and defeats sampling. Single-sample quality
# rubrics default to JUDGE_TEMPERATURE (open-ended); majority path uses this separate temp.
JUDGE_MAJORITY_TEMPERATURE = float(os.getenv("EVAL_JUDGE_MAJORITY_TEMPERATURE", "0.4"))
# Score spread across samples above this = "judge was uncertain", surfaced in reasons rather
# than silently presenting a majority-of-3 as confidently as a clean 3-0. Scale is 0–10.
JUDGE_DISAGREEMENT_SPREAD = float(os.getenv("EVAL_JUDGE_DISAGREEMENT_SPREAD", "3.5"))


async def _ask_or_majority(adapter, system: str, user: str, n_samples: int) -> Dict[str, Any]:
    """n_samples<=1: identical to a bare _ask() call (default path, unchanged).
    n_samples>1: majority-vote pass/fail across independent samples, average the scores,
    flag high-disagreement as judge uncertainty instead of reporting false confidence."""
    if n_samples <= 1:
        return await _ask(adapter, system, user)

    verdicts = await asyncio.gather(*[
        _ask(adapter, system, user, temperature=JUDGE_MAJORITY_TEMPERATURE)
        for _ in range(n_samples)
    ])
    scored = [v for v in verdicts if v["score"] is not None]
    if not scored:
        # every sample was inconclusive — don't manufacture a verdict; mark needs_rejudge.
        merged = _needs_rejudge_verdict(
            user=user, kind="empty", num_ctx=JUDGE_NUM_CTX,
            detail=f"all {n_samples} judge samples were inconclusive",
        )
        # Prefer diag from the first sample that carried one (size/timeout hint).
        for v in verdicts:
            if v.get("diag"):
                merged["diag"] = v["diag"]
                merged["reasons"] = [
                    f"needs_rejudge: all {n_samples} judge samples were inconclusive",
                    (f"diag: user_chars={v['diag'].get('user_chars')} "
                     f"(~{v['diag'].get('est_input_tokens')} tok) "
                     f"num_ctx={v['diag'].get('num_ctx')} "
                     f"likely_cause={v['diag'].get('likely_cause')}"),
                ]
                break
        return merged

    n_pass = sum(1 for v in scored if v["pass"])
    majority_pass = n_pass * 2 > len(scored)          # strict majority of the SCORED samples
    avg_score = round(sum(v["score"] for v in scored) / len(scored), 3)
    spread = max(v["score"] for v in scored) - min(v["score"] for v in scored)

    reasons: List[str] = [f"majority vote: {n_pass}/{len(scored)} samples passed "
                          f"(avg score {avg_score}, spread {round(spread, 3)})"]
    if spread >= JUDGE_DISAGREEMENT_SPREAD:
        reasons.append(f"JUDGE UNCERTAIN: scores disagreed by {round(spread, 3)} across samples "
                       f"(>= {JUDGE_DISAGREEMENT_SPREAD} threshold) — treat this verdict with less "
                       f"confidence than a low-spread majority")
    # Attach reasons from samples that match the majority verdict — the majority's own rationale,
    # not the outvoted minority's.
    for v in scored:
        if v["pass"] == majority_pass:
            reasons.extend(v["reasons"])
    return {"score": avg_score, "pass": majority_pass, "reasons": reasons}


def _wrap(rubric: str, verdict: Dict[str, Any]) -> Dict[str, Any]:
    reasons = list(verdict.get("reasons") or [])
    for label, key in (("inefficiency", "inefficiencies"), ("suggestion", "suggestions")):
        items = verdict.get(key) or []
        if isinstance(items, str):
            items = [items]
        for item in items:
            reasons.append(f"{label}: {item}")
    out: Dict[str, Any] = {
        "rubric": rubric,
        "passed": verdict["pass"],
        "score": verdict["score"],
        "reasons": reasons,
    }
    if verdict.get("needs_rejudge"):
        out["needs_rejudge"] = True
    if verdict.get("diag"):
        out["diag"] = verdict["diag"]
    if verdict.get("inefficiencies"):
        out["inefficiencies"] = verdict["inefficiencies"]
    if verdict.get("suggestions"):
        out["suggestions"] = verdict["suggestions"]
    return out


def _normalize_verdict_lists(data: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("reasons", "inefficiencies", "suggestions"):
        val = data.get(key)
        if isinstance(val, str):
            data[key] = [val]
        elif val is None and key != "reasons":
            data[key] = []
    return data


# ── Rubrics ──────────────────────────────────────────────────────────────────
# Each declares the artifact it needs. The runner skips rubrics whose input is
# absent (e.g. planner_quality without --live), with a clear note.


async def tool_appropriateness(adapter, *, prompt: str, tool_names=None, live=False, **_) -> Dict[str, Any]:
    """Did the harness pick the right tools? (dry-run: bound tools; --live: executed tools)"""
    tool_names = tool_names or []
    label = "TOOLS THE MODEL ACTUALLY CALLED" if live else "TOOLS THE HARNESS BOUND"
    tool_lines = "\n".join(f"- {n}" for n in tool_names) or "(none)"
    system = (
        "You evaluate an AI assistant's TOOL SELECTION layer. Given a user prompt and the "
        "tools the harness chose for it, judge whether the selection is appropriate: the "
        "tools needed to satisfy the prompt are present, and obviously-irrelevant tools are "
        "absent. A missing tool the task clearly needs (e.g. a delegate/subagent/skill tool "
        "for a coding or homework task) is a serious failure. Score 10 = exactly right, "
        "0 = wrong/unusable.")
    user = f"USER PROMPT:\n{prompt}\n\n{label}:\n{tool_lines}"
    return _wrap("tool_appropriateness", await _ask_or_majority(adapter, system, user, JUDGE_SAMPLES))


async def prompt_contract(adapter, *, prompt: str, system_prompt: str = "", **_) -> Dict[str, Any]:
    """Are there contradictory / conflicting instructions in the assembled system prompt?"""
    if not system_prompt:
        return {"rubric": "prompt_contract", "passed": True, "score": None,
                "reasons": ["skipped: no system_prompt artifact (use the dry-run lane, not --live)"]}
    sys_text = system_prompt[:12000]
    system = (
        "You audit an assembled SYSTEM PROMPT for internal contradictions: instructions that "
        "conflict, cancel out, or are mutually exclusive (e.g. 'always call a tool' vs 'never "
        "call tools', or duplicated/competing output formats). Score 10 = coherent and "
        "consistent, 0 = riddled with contradictions. List each contradiction in reasons.")
    user = f"ASSEMBLED SYSTEM PROMPT:\n{sys_text}"
    return _wrap("prompt_contract", await _ask(adapter, system, user))


async def planner_quality(adapter, *, prompt: str, plan: Optional[list] = None,
                          tool_names=None, events=None, gather_brief: str = "",
                          stop_reason: Optional[str] = None, response: str = "",
                          **_) -> Dict[str, Any]:
    """Is the planner's step list logically sound? (needs --live plan artifact)"""
    if not plan:
        return {"rubric": "planner_quality", "passed": True, "score": None,
                "reasons": ["skipped: no plan artifact (run with --live, Phase 1)"]}
    from evals.trajectory import pack_judge_evidence

    # Full events stay in case JSON; judge prompt caps per-event bodies to keep
    # local Gemma responsive (raw dumps can be huge even when they fit num_ctx).
    event_cap = int(os.getenv("EVAL_JUDGE_TRAJECTORY_EVENT_CHARS", "6000") or 6000)

    system = (
        "You evaluate an execution PLAN and the FULL turn trajectory that produced it. "
        "Judge: are steps ordered by dependency, concrete, and achievable with named tools? "
        "Also flag inefficiencies: re-search/re-read after gather_brief already confirmed "
        "facts/IDs; double Scout when one brief sufficed; title-as-file_id failures; "
        "website/landing-page goals with no code/HTML tool (tools=[] draft-only). "
        "ARCHITECTURE: known Drive file_id may use direct tools; open-ended explore may use "
        "`delegate_to_traversal_agent`; code/HTML uses `delegate_to_code_agent`. "
        "Score 10 = sound AND efficient; each inefficiency must dock points (see scoring rules)."
    )
    user = pack_judge_evidence(
        prompt=prompt,
        events=events,
        tool_names=tool_names,
        plan=plan,
        gather_brief=gather_brief,
        stop_reason=stop_reason,
        response=response,
        max_event_chars=event_cap,
    )
    return _wrap("planner_quality", await _ask(adapter, system, user))


# Canned pause text from TurnResponsePostProcessor when final_response was emptied after
# stripping plan JSON (see src/core/orchestration/turn/response_processor.py).
_PLAN_APPROVAL_STUB_MARKERS = (
    "proposed execution plan",
    "review the steps above",
)


def _is_plan_approval_pause(*, response: str = "", plan=None,
                            stop_reason: Optional[str] = None) -> bool:
    """True when the turn's real deliverable is the plan card, not chat text."""
    if not plan:
        return False
    if stop_reason in ("awaiting_approval", "awaiting_input"):
        return True
    r = (response or "").strip().lower()
    return bool(r) and all(m in r for m in _PLAN_APPROVAL_STUB_MARKERS)


async def response_grounded(adapter, *, prompt: str, response: str = "",
                            tool_results: str = "", plan: Optional[list] = None,
                            stop_reason: Optional[str] = None,
                            tool_names=None, events=None, gather_brief: str = "",
                            **_) -> Dict[str, Any]:
    """Did the final answer follow from tool results, or hallucinate? (needs --live)

    Plan-approval pauses are special: the Brain deliberately strips plan JSON from chat
    text and emits a canned stub ("Here's the proposed execution plan…") while the real
    deliverable is the ``ExecutionPlanEvent`` card. Scoring that stub as the "response"
    falsely fails every healthy plan pause (HITL 2026-07-24). When we detect a plan pause,
    ground the **plan steps** against the full trajectory instead of the stub.
    """
    from evals.trajectory import pack_judge_evidence

    if _is_plan_approval_pause(response=response, plan=plan, stop_reason=stop_reason):
        if not plan:
            return {"rubric": "response_grounded", "passed": False, "score": 0.0,
                    "reasons": ["plan-approval pause but no plan artifact to judge"]}
        system = (
            "You judge whether a proposed EXECUTION PLAN is grounded in the FULL trajectory "
            "(thoughts, tools, gather_brief) and addresses the user. Ignore the chat stub — "
            "the PLAN is the deliverable. Flag inefficiencies (rediscovery after brief, "
            "duplicate reads, bad file_ids). Unsupported steps = failure. "
            "Score 10 = grounded, on-request, and reasonably efficient; dock for waste."
        )
        user = pack_judge_evidence(
            prompt=prompt,
            events=events,
            tool_names=tool_names,
            plan=plan,
            gather_brief=gather_brief,
            stop_reason=stop_reason,
            response=response,
            max_event_chars=int(os.getenv("EVAL_JUDGE_TRAJECTORY_EVENT_CHARS", "6000") or 6000),
        )
        return _wrap("response_grounded", await _ask(adapter, system, user))

    if not response:
        return {"rubric": "response_grounded", "passed": True, "score": None,
                "reasons": ["skipped: no response artifact (run with --live, Phase 1)"]}
    system = (
        "You judge whether an assistant's final RESPONSE is grounded in the available evidence "
        "(full trajectory / tool results) and actually answers the user. Unsupported claims = failure. "
        "Treating tool absence as global proof of non-action is ungrounded. "
        "If the user asked for a full message/body/details and the RESPONSE quotes text that "
        "clearly looks like a list/inbox PREVIEW (cut off mid-sentence, dangling clause, "
        "ellipsis, or ending mid-word) without opening the thread/detail view first, that is "
        "a FAIL even when the truncated snippet appears in tool results — grounded but incomplete. "
        "Also note inefficiencies in suggestions. "
        "Score 10 = fully grounded and responsive, 0 = hallucinated, non-responsive, or "
        "preview-as-full-content.")
    if events:
        user = pack_judge_evidence(
            prompt=prompt,
            events=events,
            tool_names=tool_names,
            plan=plan,
            gather_brief=gather_brief,
            stop_reason=stop_reason,
            response=response,
            max_event_chars=int(os.getenv("EVAL_JUDGE_TRAJECTORY_EVENT_CHARS", "6000") or 6000),
        )
    else:
        if JUDGE_USER_CHAR_BUDGET <= 0:
            tr_txt = coalesce_tool_results_for_judge(tool_results or "") or "(none)"
            resp_txt = response
        else:
            tr_txt = coalesce_tool_results_for_judge(tool_results, max_chars=12000) or "(none)"
            resp_txt = _truncate(response, 4000, "response")
        user = f"USER PROMPT:\n{prompt}\n\nTOOL RESULTS:\n{tr_txt}\n\nRESPONSE:\n{resp_txt}"
    return _wrap("response_grounded", await _ask(adapter, system, user))


async def workflow_outcome(adapter, *, prompt: str, plan: Optional[list] = None,
                           tool_names=None, response: str = "", tool_results: str = "",
                           completed: bool = False, **_) -> Dict[str, Any]:
    """Did the FULL workflow (plan → approve → execute) actually accomplish the goal?

    Needs --workflow: judges the whole multi-turn run, not a single turn. Looks at the
    plan, the tools the model executed across all rounds, and the final response together —
    catches a workflow that produced a nice plan but then stalled, skipped steps, or
    declared success without doing the work.
    """
    if not response and not tool_names:
        return {"rubric": "workflow_outcome", "passed": True, "score": None,
                "reasons": ["skipped: no workflow artifacts (run with --workflow)"]}
    tools = ", ".join(tool_names or []) or "(none executed)"
    plan_txt = json.dumps(plan, indent=2) if plan else "(no plan captured)"
    prompt_b, plan_b, tools_b, results_b, response_b = _budget_workflow_text(
        prompt=prompt, plan_txt=plan_txt, tools=tools,
        tool_results=tool_results or "(none)", response=response)
    system = (
        "You evaluate whether an AI assistant COMPLETED a multi-step workflow. You are given "
        "the user's goal, the plan it proposed, the tools it actually executed (in order, across "
        "all turns), and its final response. Judge whether the goal was genuinely achieved: the "
        "plan's steps were carried out with appropriate tools and the final response reflects real "
        "work — not a premature 'done', a skipped/abandoned step, or a stall. "
        f"The harness reports completed={completed}. "
        "Score 10 = goal fully accomplished, 0 = claimed/partial but not actually done.")
    user = (f"USER GOAL:\n{prompt_b}\n\nPROPOSED PLAN:\n{plan_b}\n\n"
            f"TOOLS EXECUTED (all rounds):\n{tools_b}\n\n"
            f"TOOL RESULTS:\n{results_b}\n\nFINAL RESPONSE:\n{response_b}")
    return _wrap("workflow_outcome", await _ask_or_majority(adapter, system, user, JUDGE_SAMPLES))


JUDGES = {
    "tool_appropriateness": tool_appropriateness,
    "prompt_contract": prompt_contract,
    "planner_quality": planner_quality,
    "response_grounded": response_grounded,
    "workflow_outcome": workflow_outcome,
}
