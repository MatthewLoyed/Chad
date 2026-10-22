r"""
checks.py - one function per external dependency, plus the CHECKS registry.

DESIGN RULES (these are what make the gate worth trusting)
----------------------------------------------------------
1. Every check does a REAL round-trip. "The port is open" is not a check; "a query came
   back with >0 results" is. Shape-only checks are what let the empty-Brave run happen.
2. No check may ever block on human interaction. In particular the Google check must
   NEVER fall through to `InstalledAppFlow` - that interactive browser prompt IS the
   failure mode we are detecting.
3. Every check is bounded by a short timeout and reports its own elapsed time, so
   preflight itself can never become the thing that hangs a run.
4. ASCII only in any string that reaches `detail`/`remedy` - this prints to a cp1252
   Windows console, where a stray em-dash renders as a replacement char.

This module is expected to keep growing (one function per dependency); the orchestration
in gate.py is stable. That seam is why they are separate files.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from evals.preflight.capabilities import _registered_tool_names, _tool_exists
from evals.preflight.model import (FAIL, OK, PROJECT_ROOT, SKIP, WARN, CheckResult,
                                       PreflightContext)

def _http_json(url: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _cfg(path: str, default: Any = None) -> Any:
    try:
        from src.shared.config_manager import ConfigManager
        return ConfigManager.get(path, default)
    except Exception:
        return default


def _secret(name: str) -> Optional[str]:
    try:
        from src.shared.config_manager import ConfigManager
        return ConfigManager.get_secret(name)
    except Exception:
        return os.environ.get(name)



def check_datasets(ctx: PreflightContext) -> CheckResult:
    """Every selected case must be *satisfiable*: valid JSON, known expect keys, known
    judge rubrics, unique ids, and - critically - no hard tool requirement naming a tool
    that does not exist.

    An `expect.tools_include: ["get_time"]` where the real tool is `get_datetime` is a
    case that can never pass no matter how good the model is. That silently reads as a
    capability failure forever, which is the worst possible eval data.
    """
    facts: Dict[str, Any] = {}
    problems: List[str] = []
    warnings: List[str] = []

    if ctx.load_errors:
        facts["load_errors"] = ctx.load_errors
        problems.append(
            "suite(s) failed to parse and would be silently skipped: "
            + "; ".join(f"{s} ({e})" for s, e in sorted(ctx.load_errors.items()))
        )

    # Duplicate ids across the selected suites collapse in per-case history tracking.
    seen: Dict[str, int] = {}
    for case in ctx.cases:
        cid = getattr(case, "id", None) or "?"
        seen[cid] = seen.get(cid, 0) + 1
    dupes = sorted(c for c, n in seen.items() if n > 1)
    if dupes:
        problems.append(f"duplicate case ids: {dupes}")
    facts["n_cases"] = len(ctx.cases)

    known = _registered_tool_names()
    facts["registry_tools"] = len(known) if known is not None else None
    if known:
        unknown_hard: List[str] = []
        unknown_soft: Set[str] = set()
        for case in ctx.cases:
            expect = getattr(case, "expect", None) or {}
            for key in ("tools_include", "scout_tools_include", "plan_tools_include"):
                for tool in expect.get(key, []) or []:
                    if not _tool_exists(tool, known):
                        unknown_hard.append(f"{getattr(case, 'id', '?')}.{key}:{tool}")
            for key in ("tools_any", "scout_tools_any", "plan_tools_any"):
                for tool in expect.get(key, []) or []:
                    if not _tool_exists(tool, known):
                        unknown_soft.add(tool)
        if unknown_hard:
            problems.append(
                f"{len(unknown_hard)} hard tool requirement(s) name a tool that does not "
                f"exist - those cases can NEVER pass: {unknown_hard[:6]}"
            )
        if unknown_soft:
            # In a `tools_any` list an unknown name is usually a harmless alias sitting
            # next to the real tool, so this is advisory.
            warnings.append(f"unknown tool name(s) in tools_any (harmless if aliases): "
                            f"{sorted(unknown_soft)[:8]}")
        facts["unknown_hard_tools"] = len(unknown_hard)
        facts["unknown_any_tools"] = len(unknown_soft)

    if problems:
        return CheckResult("datasets", FAIL, "; ".join(problems),
                           remedy="Fix the dataset lines above - a case with an impossible "
                                  "assertion produces permanently misleading eval data.",
                           facts=facts)
    if warnings:
        return CheckResult("datasets", WARN, "; ".join(warnings), facts=facts)
    return CheckResult("datasets", OK,
                       f"{len(ctx.cases)} case(s) satisfiable; all expect keys, rubrics and "
                       f"tool names resolve", facts=facts)



def _lmstudio_base_url() -> str:
    """Resolve the LM Studio OpenAI-compatible base URL the same way the app does."""
    for key in ("local_models.active_model.base_url", "local_models.base_url"):
        val = _cfg(key)
        if val:
            return str(val).rstrip("/")
    return "http://localhost:1234/v1"


def check_lmstudio(ctx: PreflightContext) -> CheckResult:
    base = _lmstudio_base_url()
    try:
        data = _http_json(f"{base}/models", timeout=5.0)
    except Exception as e:
        return CheckResult("lmstudio", FAIL, f"{base}/models unreachable: {type(e).__name__}: {e}",
                           remedy="Start LM Studio and enable the local server (port 1234).")
    ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
    return CheckResult("lmstudio", OK, f"reachable, {len(ids)} model(s) served",
                       facts={"base_url": base, "served_models": ids})


def check_judge_model(ctx: PreflightContext) -> CheckResult:
    """The judge model must already be SERVED, not JIT-loadable.

    Naming an unloaded id makes LM Studio JIT-load it mid-run, which under VRAM pressure
    both stalls the run and (with Auto-Evict on) can silently evict the model the Brain
    is using - issue #592. We also refuse heavy ids outright.
    """
    model = ctx.judge_model
    if not model or model in ("local", "cloud"):
        return CheckResult("judge_model", SKIP, f"judge model follows config ({model!r})")

    base = _lmstudio_base_url()
    try:
        data = _http_json(f"{base}/models", timeout=5.0)
    except Exception as e:
        return CheckResult("judge_model", FAIL, f"cannot list models: {type(e).__name__}: {e}",
                           remedy="Start LM Studio's local server.")
    ids = {m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)}

    heavy = [h for h in ("qwen3.6-35b", "qwen3-coder-30b", "70b", "72b") if h in model.lower()]
    if heavy:
        return CheckResult("judge_model", FAIL,
                           f"judge model {model!r} looks heavy ({heavy[0]}) - it will hold VRAM "
                           f"for the whole run and starve the model under test",
                           remedy="Pin a small loaded judge, e.g. --judge-model qwen/qwen3.5-9b.",
                           facts={"judge_model": model})
    if model not in ids:
        return CheckResult("judge_model", FAIL,
                           f"judge model {model!r} is NOT currently served (served: "
                           f"{sorted(ids)[:6]}{'...' if len(ids) > 6 else ''})",
                           remedy=f"Load {model} in LM Studio first, or pass a served id. "
                                  f"Leaving it unloaded makes LM Studio JIT-load it mid-run "
                                  f"(issue #592: Auto-Evict then unloads the model under test).",
                           facts={"judge_model": model, "served_models": sorted(ids)})
    return CheckResult("judge_model", OK, f"{model} is served",
                       facts={"judge_model": model})


def check_server(ctx: PreflightContext) -> CheckResult:
    url = ctx.server_health_url
    if not url:
        try:
            from evals.live import HEALTH_URL
            url = HEALTH_URL
        except Exception:
            return CheckResult("server", FAIL, "could not derive the Brain health URL",
                               remedy="Set CHAD_SERVER_WS or check evals/live.py.")
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=5.0) as r:
            status = r.status
            body = r.read(2000).decode("utf-8", "replace")
    except Exception as e:
        return CheckResult("server", FAIL, f"{url} unreachable: {type(e).__name__}: {e}",
                           remedy="Start the Brain (prod :8767 / dev :8788) before a "
                                  "--live/--workflow run.",
                           elapsed_s=round(time.time() - t0, 2), facts={"health_url": url})
    if status != 200:
        return CheckResult("server", FAIL, f"{url} returned HTTP {status}",
                           remedy="Check the Brain logs.", facts={"health_url": url})

    facts: Dict[str, Any] = {"health_url": url}
    try:
        facts.update({k: v for k, v in json.loads(body).items()
                      if k in ("version", "model", "mode", "git_sha", "uptime_s")})
    except Exception:
        pass
    return CheckResult("server", OK, f"healthy at {url}"
                       + (f" (model={facts['model']})" if facts.get("model") else ""),
                       elapsed_s=round(time.time() - t0, 2), facts=facts)


def check_auth_token(ctx: PreflightContext) -> CheckResult:
    try:
        from evals.live import load_auth_token
        token = load_auth_token()
    except Exception as e:
        return CheckResult("auth_token", FAIL, f"could not load auth token: {e}",
                           remedy="Set CHAD_REMOTE_TOKEN in .env.")
    if not token:
        return CheckResult("auth_token", FAIL, "no CHAD_REMOTE_TOKEN / CHAD_SERVER_TOKEN found",
                           remedy="Add CHAD_REMOTE_TOKEN to .env - otherwise every live case "
                                  "fails identically at the auth handshake.")
    return CheckResult("auth_token", OK, f"present ({len(token)} chars)")


# Google refresh tokens issued by an OAuth app in "Testing" publish status expire ~7 days
# after CONSENT (issue #476).
_GOOGLE_TESTING_TOKEN_LIFETIME_S = 7 * 24 * 3600
_GOOGLE_WARN_HEADROOM_S = 12 * 3600

# Nothing in token.json records when the refresh token was issued, and neither file
# timestamp is a usable proxy:
#
#   * mtime resets every time an ACCESS token is refreshed (GoogleWorkspaceManager rewrites
#     the file hourly), so it always looks fresh - dangerously optimistic, and exactly the
#     false confidence that lets an 8-hour run start on a credential about to die;
#   * ctime/birthtime never moves, because re-consent rewrites the same path in place
#     (open(path, "w") truncates, preserving the inode) - permanently pessimistic.
#
# So we keep our own sidecar: the first time we see a given refresh-token value we record
# the timestamp. The value rotates on re-consent, which resets the clock correctly and
# automatically. Only a hash is stored - never the token itself.
_OAUTH_SEEN_PATH = Path(__file__).resolve().parents[1] / "runs" / ".oauth_seen.json"


def _refresh_token_first_seen(refresh_token: str) -> tuple:
    """(first_seen_epoch, is_first_observation) for this refresh-token value.

    Returns (None, True) the very first time a token is observed: we genuinely do not know
    its age yet, and saying so beats inventing a number.
    """
    digest = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:16]
    now = time.time()
    seen: Dict[str, float] = {}
    try:
        if _OAUTH_SEEN_PATH.exists():
            loaded = json.loads(_OAUTH_SEEN_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                seen = {k: v for k, v in loaded.items() if isinstance(v, (int, float))}
    except Exception:
        seen = {}

    if digest in seen:
        return seen[digest], False

    # Prune stale entries so the file can't grow without bound, then record this one.
    cutoff = now - 90 * 24 * 3600
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    seen[digest] = now
    try:
        _OAUTH_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OAUTH_SEEN_PATH.write_text(json.dumps(seen), encoding="utf-8")
    except Exception:
        pass          # best-effort: an unwritable sidecar must not block the run
    return now, True


def check_google_oauth(ctx: PreflightContext) -> CheckResult:
    """Prove Gmail/Calendar/Drive will work for the WHOLE run - without ever prompting.

    This is the check that exists because of issue #476: a dead refresh token turned an
    unattended sweep into hours of ~100s interactive-OAuth hangs and contaminated the
    results. We therefore (a) force a real refresh, which is the only thing that surfaces
    `invalid_grant`, (b) make one real API call, and (c) compare the credential's
    remaining life against the estimated run duration.
    """
    token_path = PROJECT_ROOT / "config" / "token.json"
    facts: Dict[str, Any] = {"token_path": str(token_path)}

    if not token_path.exists():
        if _secret("GOOGLE_TOKEN_JSON"):
            return CheckResult("google_oauth", WARN,
                              "no config/token.json on disk; GOOGLE_TOKEN_JSON env is set and "
                              "will be bootstrapped at first use (not verified here)",
                              remedy="Run a Google tool once to materialize token.json, then "
                                     "re-run preflight for a real verification.", facts=facts)
        return CheckResult("google_oauth", FAIL, "config/token.json is missing",
                           remedy="Authorize once: python -m src.scripts.generate_token",
                           facts=facts)

    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.credentials import Credentials
        from src.tools.workspace.manager import SCOPES
    except Exception as e:
        return CheckResult("google_oauth", WARN,
                           f"google auth libraries unavailable ({type(e).__name__}), cannot verify",
                           remedy="pip install google-auth google-auth-oauthlib "
                                  "google-api-python-client",
                           facts=facts)

    t0 = time.time()
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except Exception as e:
        return CheckResult("google_oauth", FAIL, f"token.json is unreadable: {e}",
                           remedy="Re-authorize: python -m src.scripts.generate_token",
                           facts=facts)

    if not creds.refresh_token:
        return CheckResult("google_oauth", FAIL,
                           "token.json has no refresh_token - it cannot survive the access "
                           "token expiring mid-run",
                           remedy="Re-authorize: python -m src.scripts.generate_token",
                           facts=facts)

    # Force a refresh. NEVER fall through to InstalledAppFlow - an interactive browser
    # prompt is precisely the failure this check prevents.
    try:
        creds.refresh(GoogleRequest())
    except Exception as e:
        return CheckResult("google_oauth", FAIL,
                           f"refresh FAILED: {type(e).__name__}: {e}",
                           remedy="Re-authorize (python -m src.scripts.generate_token). "
                                  "Permanent fix = move the OAuth app from Testing to "
                                  "Production publish status so refresh tokens stop expiring "
                                  "every 7 days (issue #476).",
                           elapsed_s=round(time.time() - t0, 2), facts=facts)

    # A refresh can succeed while a scope is missing, so make one real call.
    try:
        from googleapiclient.discovery import build
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = drive.about().get(fields="user(emailAddress)").execute()
        facts["account"] = (about.get("user") or {}).get("emailAddress")
    except Exception as e:
        return CheckResult("google_oauth", FAIL,
                           f"refresh worked but a live Drive call failed: {type(e).__name__}: {e}",
                           remedy="Scopes may have changed - re-authorize to re-consent.",
                           elapsed_s=round(time.time() - t0, 2), facts=facts)

    # Will the credential outlive the run? Age is measured from the first time WE saw this
    # refresh-token value (see _refresh_token_first_seen for why no file timestamp works).
    elapsed = round(time.time() - t0, 2)
    acct = facts.get("account") or "unknown account"
    first_seen, is_new = _refresh_token_first_seen(creds.refresh_token)
    age_s = max(0.0, time.time() - first_seen)
    remaining_s = _GOOGLE_TESTING_TOKEN_LIFETIME_S - age_s
    facts.update({"token_age_hours": round(age_s / 3600, 1),
                  "age_known": not is_new,
                  "estimated_run_hours": round(ctx.estimated_run_s / 3600, 2)})

    if is_new:
        # First observation of this token: it might have been issued a minute ago or six
        # days ago. Report the uncertainty instead of a fabricated horizon - a confident
        # wrong number here is what starts an 8-hour run on a dying credential.
        facts["est_remaining_hours"] = None
        return CheckResult("google_oauth", WARN,
                           f"refresh + live Drive call OK ({acct}), but this refresh token is "
                           f"new to preflight so its AGE IS UNKNOWN - it could be up to 7 days "
                           f"old and expire mid-run. From now on its age is tracked.",
                           remedy="If this run is long and unattended, re-authorize first "
                                  "(python -m src.scripts.generate_token) to start the 7-day "
                                  "clock from a known point. Permanent fix: move the OAuth app "
                                  "from Testing to Production publish status (issue #476).",
                           elapsed_s=elapsed, facts=facts)

    facts["est_remaining_hours"] = round(remaining_s / 3600, 1)
    if remaining_s <= 0:
        return CheckResult("google_oauth", WARN,
                           f"refresh works now ({acct}) but this refresh token is "
                           f"{age_s / 86400:.1f} days old - past the 7-day Testing-status "
                           f"lifetime, so it may die at any moment mid-run",
                           remedy="Re-authorize NOW before starting a long run, or fix #476 "
                                  "permanently (OAuth app -> Production publish status).",
                           elapsed_s=elapsed, facts=facts)
    if ctx.estimated_run_s and remaining_s < ctx.estimated_run_s + _GOOGLE_WARN_HEADROOM_S:
        return CheckResult("google_oauth", FAIL,
                           f"credential works ({acct}) but only ~{remaining_s / 3600:.1f}h of "
                           f"refresh-token life remains, and this run needs ~"
                           f"{ctx.estimated_run_s / 3600:.1f}h - it would die partway through "
                           f"and contaminate the results",
                           remedy="Re-authorize before starting (python -m "
                                  "src.scripts.generate_token), or fix #476 permanently.",
                           elapsed_s=elapsed, facts=facts)
    return CheckResult("google_oauth", OK,
                       f"refresh + live Drive call OK ({acct}); ~{remaining_s / 3600:.1f}h of "
                       f"tracked refresh-token life left", elapsed_s=elapsed, facts=facts)


def check_web_search(ctx: PreflightContext) -> CheckResult:
    """A search backend that answers but returns ZERO hits fails every research case
    while looking perfectly healthy. So assert on hit COUNT, not on reachability.
    """
    backend = str(_cfg("tools.search_backend", "searxng"))
    url = str(_cfg("tools.searxng_url", "http://127.0.0.1:8080")).rstrip("/")
    facts: Dict[str, Any] = {"backend": backend, "searxng_url": url}
    probe = "nvidia stock price"
    t0 = time.time()

    hits = None
    detail = ""
    try:
        q = urllib.parse.urlencode({"q": probe, "format": "json"})
        data = _http_json(f"{url}/search?{q}", timeout=12.0)
        hits = len(data.get("results") or []) if isinstance(data, dict) else 0
        facts["searxng_hits"] = hits
    except Exception as e:
        detail = f"SearXNG probe failed: {type(e).__name__}: {e}"

    if hits:
        return CheckResult("web_search", OK,
                           f"SearXNG returned {hits} hit(s) for {probe!r}",
                           elapsed_s=round(time.time() - t0, 2), facts=facts)

    # SearXNG empty or down - is there a working paid fallback?
    brave = _secret("BRAVE_API_KEY")
    facts["brave_key_present"] = bool(brave)
    if brave:
        try:
            req = urllib.request.Request(
                "https://api.search.brave.com/res/v1/web/search?"
                + urllib.parse.urlencode({"q": probe, "count": "3"}),
                headers={"Accept": "application/json", "X-Subscription-Token": brave})
            with urllib.request.urlopen(req, timeout=12.0) as r:
                payload = json.loads(r.read().decode("utf-8", "replace"))
            bhits = len((payload.get("web") or {}).get("results") or [])
            facts["brave_hits"] = bhits
            if bhits:
                return CheckResult("web_search", WARN,
                                   f"SearXNG returned 0 hits ({detail or 'empty'}) but Brave "
                                   f"fallback returned {bhits} - research cases will work, "
                                   f"just via the paid path",
                                   remedy="Restart the SearXNG sidecar: docker compose -f "
                                          "docker/searxng/docker-compose.yml up -d",
                                   elapsed_s=round(time.time() - t0, 2), facts=facts)
            detail = (detail + "; " if detail else "") + "Brave returned 0 hits"
        except Exception as e:
            detail = (detail + "; " if detail else "") + f"Brave probe failed: {type(e).__name__}"

    return CheckResult("web_search", FAIL,
                       f"no search backend returns results ({detail or 'SearXNG returned 0 hits'})",
                       remedy="Bring up SearXNG (docker compose -f "
                              "docker/searxng/docker-compose.yml up -d) or set a working "
                              "BRAVE_API_KEY. Running research cases against an empty backend "
                              "produces a wall of fake 'model failed' results.",
                       elapsed_s=round(time.time() - t0, 2), facts=facts)


def check_gate(ctx: PreflightContext) -> CheckResult:
    """lmstudio-gate must be alive AND not serializing everything to 1 concurrent request.

    A gate with max_concurrent=1 silently turns every parallel fan-out eval into a serial
    one, which reads as a latency/timeout regression rather than a config problem (#510).
    """
    try:
        from src.infrastructure.llm.gate_client import gate_alive, gate_enabled, gate_port
    except Exception as e:
        return CheckResult("gate", SKIP, f"gate client unavailable ({type(e).__name__})")
    if not gate_enabled():
        return CheckResult("gate", SKIP, "lmstudio-gate disabled in config")
    if not gate_alive():
        return CheckResult("gate", WARN, "lmstudio-gate not responding; judge calls go direct "
                                         "and will contend with real Brain turns",
                           remedy="Let the runner start it, or start the Brain first.")
    facts: Dict[str, Any] = {"gate_port": gate_port()}
    try:
        health = _http_json(f"http://127.0.0.1:{gate_port()}/gate/health", timeout=2.0)
        if isinstance(health, dict):
            # PriorityGate.stats exposes {"active", "waiting", "max"}.
            facts.update({k: health[k] for k in ("active", "waiting", "max", "upstream")
                          if k in health})
    except Exception:
        pass
    mc = facts.get("max")
    if isinstance(mc, int) and mc <= 1:
        return CheckResult("gate", WARN,
                           f"gate alive but max concurrency={mc} - ALL local inference is "
                           f"serialized, so any parallelism eval will look broken (#510)",
                           remedy="Raise local_models.gate.max_concurrent (6 is the verified "
                                  "value) before measuring fan-out.", facts=facts)
    return CheckResult("gate", OK, f"alive on :{facts['gate_port']}"
                       + (f", max_concurrent={mc}" if mc else ""), facts=facts)


def check_sandbox(ctx: PreflightContext) -> CheckResult:
    """A dead sandbox was 27 of 28 'the 14B can't do workflows' failures. Verify it runs code."""
    key = _secret("E2B_API_KEY")
    if not key:
        return CheckResult("sandbox", FAIL, "no E2B_API_KEY configured",
                           remedy="Set E2B_API_KEY, or drop the sandbox cases from this run. "
                                  "A dead sandbox reads as a model failure on every "
                                  "run_sandboxed_* case.")
    return CheckResult("sandbox", WARN,
                       "E2B_API_KEY present but not exercised (a real spin-up costs quota)",
                       remedy="Run one sandbox case manually first if this sweep depends on it.",
                       facts={"e2b_key_present": True})


def check_docker(ctx: PreflightContext) -> CheckResult:
    exe = shutil.which("docker")
    if not exe:
        return CheckResult("docker", FAIL, "docker not on PATH",
                           remedy="Start Docker Desktop.")
    t0 = time.time()
    try:
        proc = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return CheckResult("docker", FAIL, "docker info timed out after 20s",
                           remedy="Docker Desktop is starting or wedged - restart it.")
    if proc.returncode != 0:
        return CheckResult("docker", FAIL,
                           f"docker daemon not responding: {(proc.stderr or '').strip()[:200]}",
                           remedy="Start Docker Desktop.")
    return CheckResult("docker", OK, f"daemon up (server {proc.stdout.strip()})",
                       elapsed_s=round(time.time() - t0, 2))


def _binary_check(name: str, exe: str, remedy: str) -> Callable[[PreflightContext], CheckResult]:
    def _check(ctx: PreflightContext) -> CheckResult:
        found = shutil.which(exe)
        if not found:
            return CheckResult(name, FAIL, f"{exe} not on PATH", remedy=remedy)
        return CheckResult(name, OK, f"{exe} found at {found}", facts={"path": found})
    return _check


def check_supabase(ctx: PreflightContext) -> CheckResult:
    url, key = _secret("SUPABASE_URL"), _secret("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return CheckResult("supabase", SKIP, "not configured (local-first default)")
    # A placeholder service_role key 401s every call and previously broke the whole GUI
    # send path, so verify with a real request rather than trusting the key's presence.
    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/",
                                     headers={"apikey": key, "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=8.0) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return CheckResult("supabase", FAIL, f"credentials rejected (HTTP {e.code})",
                               remedy="The service_role key is wrong or a placeholder - fix "
                                      "SUPABASE_SERVICE_ROLE_KEY in .env.")
        code = e.code
    except Exception as e:
        return CheckResult("supabase", WARN, f"unreachable: {type(e).__name__}: {e}",
                           remedy="Cloud features will degrade; local-first paths still work.")
    return CheckResult("supabase", OK, f"reachable (HTTP {code})")


def check_disk(ctx: PreflightContext) -> CheckResult:
    """Transcripts for a long sweep are not small, and running out of space mid-run loses
    exactly the artifacts you needed to explain the failures."""
    runs_dir = Path(__file__).resolve().parent / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(runs_dir)
    free_gb = usage.free / 1e9
    facts = {"free_gb": round(free_gb, 1)}
    if free_gb < 1.0:
        return CheckResult("disk", FAIL, f"only {free_gb:.1f} GB free on the evals volume",
                           remedy="Free space - transcripts will be truncated or lost.",
                           facts=facts)
    if free_gb < 5.0:
        return CheckResult("disk", WARN, f"{free_gb:.1f} GB free", facts=facts)
    return CheckResult("disk", OK, f"{free_gb:.1f} GB free", facts=facts)


def check_telegram(ctx: PreflightContext) -> CheckResult:
    token = _secret("TELEGRAM_BOT_TOKEN")
    if not token:
        return CheckResult("telegram", FAIL, "no TELEGRAM_BOT_TOKEN configured",
                           remedy="Set TELEGRAM_BOT_TOKEN or drop the telegram cases.")
    try:
        data = _http_json(f"https://api.telegram.org/bot{token}/getMe", timeout=8.0)
        if not (isinstance(data, dict) and data.get("ok")):
            raise RuntimeError(str(data)[:120])
    except Exception as e:
        return CheckResult("telegram", FAIL, f"getMe failed: {type(e).__name__}: {e}",
                           remedy="Token is invalid or revoked.")
    return CheckResult("telegram", OK, "bot token valid")


def check_canvas(ctx: PreflightContext) -> CheckResult:
    token = _secret("CANVAS_API_TOKEN") or _secret("CANVAS_TOKEN")
    if not token:
        return CheckResult("canvas", FAIL, "no Canvas API token configured",
                           remedy="Set CANVAS_API_TOKEN or drop the Canvas cases - they will "
                                  "otherwise fail uniformly and look like model errors.")
    return CheckResult("canvas", WARN, "Canvas token present but not exercised",
                       facts={"canvas_token_present": True})


def check_smart_home(ctx: PreflightContext) -> CheckResult:
    if not (_secret("HUBSPACE_USERNAME") and _secret("HUBSPACE_PASSWORD")):
        return CheckResult("smart_home", FAIL, "Hubspace credentials not configured",
                           remedy="Set HUBSPACE_USERNAME/HUBSPACE_PASSWORD or drop those cases.")
    return CheckResult("smart_home", WARN, "Hubspace credentials present but not exercised")


def check_browser(ctx: PreflightContext) -> CheckResult:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return CheckResult("browser", FAIL, "playwright not importable",
                           remedy="pip install playwright && playwright install chromium")
    return CheckResult("browser", OK, "playwright importable")


CHECKS: Dict[str, Callable[[PreflightContext], CheckResult]] = {
    "datasets": check_datasets,
    "lmstudio": check_lmstudio,
    "judge_model": check_judge_model,
    "server": check_server,
    "auth_token": check_auth_token,
    "google_oauth": check_google_oauth,
    "web_search": check_web_search,
    "gate": check_gate,
    "sandbox": check_sandbox,
    "docker": check_docker,
    "supabase": check_supabase,
    "disk": check_disk,
    "telegram": check_telegram,
    "canvas": check_canvas,
    "smart_home": check_smart_home,
    "browser": check_browser,
    "typst": _binary_check("typst", "typst",
                           "Install typst (winget install typst) or drop compile_typst cases."),
    "marp": _binary_check("marp", "marp",
                          "npm i -g @marp-team/marp-cli, or drop the marp/presentation cases."),
}

# Checks worth running on every invocation regardless of what the cases need.
_ALWAYS: Set[str] = {"datasets", "disk"}

