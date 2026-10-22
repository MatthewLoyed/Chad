"""OpenHands dispatch — Chad Code's coding-harness bridge (epic #569, issue #447).

Chad Code delegates real coding work to **OpenHands** running a **local model** (via LM
Studio), 24/7 with no cloud usage limits. This module is the Chad-side "launcher +
reporter".

It uses OpenHands' **headless SDK** path — a single detached ``docker run`` that executes
``docker/openhands/chad_headless_runner.py`` inside the image against a mounted workspace.
This deliberately avoids the OpenHands *app-server* (its Windows sandbox-startup
``exposed_urls`` race blocks that path — see #447); the headless path was proven end-to-end
2026-07-20. The tradeoff is no live web-UI to steer the run; dispatch is fire-and-report.

Two tools:
  - ``dispatch_coding_task`` — ensure model resident → launch a detached container → return
    a job handle (job id + workspace path). Returns without waiting for the coding job to
    finish; it may wait briefly (awaited, not blocking the event loop) for the model to
    become resident on a cold load.
  - ``check_coding_task``    — report a job's status, transcript tail, and workspace files.

Notes learned live (2026-07-20):
  - Legacy Qwen uses text parsing. ``openhands_native_tool_calling_by_model`` enables
    native OpenAI tool_calls for models such as LFM2.5 without changing that baseline.
  - Windows Docker bind mounts need ``C:/...`` paths, not ``/mnt/c/...``.
  - The container reaches LM Studio via ``host.docker.internal:1234``; Chad's own model-ensure
    uses ``localhost:1234``.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool

logger = setup_logger("OpenHandsDispatch")

# Defaults — overridable via config.yaml `tools.openhands_*`.
_DEFAULT_IMAGE = "docker.openhands.dev/openhands/openhands:latest"
# Custom layer (docker/openhands/Dockerfile.codegraph) adding Node + the codegraph MCP CLI on
# top of the base image — see codegraph section below. Only used for reference-mounted jobs.
_DEFAULT_CODEGRAPH_IMAGE = "chad-code/openhands-codegraph:latest"
_DEFAULT_MODEL = "openai/qwen/qwen3.6-35b-a3b"
_DEFAULT_HOST_LM_URL = "http://localhost:1234/v1"                  # Chad (host) -> LM Studio
_DEFAULT_CONTAINER_LM_URL = "http://host.docker.internal:1234/v1"  # container -> LM Studio
_DEFAULT_MAX_ITER = 30
_DEFAULT_NATIVE_TOOL_CALLING = False  # mid-size local coders emit Hermes text, not tool_calls
_DEFAULT_JOB_TTL_HOURS = 24           # stopped job containers older than this get swept
_DEFAULT_JOB_TIMEOUT_S = 1800         # wall-clock cap per job; runner self-kills past this
_CONTAINER_PREFIX = "chad-code-"
_CONTAINER_LABEL = "chad-code"        # every job container carries this for safe pruning
_DOCKER_TIMEOUT_S = 60.0
_DONE_SENTINEL = "CHAD_DISPATCH_DONE"
_ERR_SENTINEL = "CHAD_DISPATCH_ERROR"

# GitHub's hosted Streamable-HTTP MCP endpoint. Enabled only when tools.openhands_github_mcp is
# True AND a real PAT is present (the base image has no npx/uvx, so a stdio GitHub MCP won't run —
# the hosted HTTP server is the only viable path). Off by default.
_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
# Context7's hosted MCP — documentation lookup (`resolve-library-id`, `query-docs`). Streamable
# HTTP, no key or npx needed; verified reachable from the container. On by default.
_CONTEXT7_MCP_URL = "https://mcp.context7.com/mcp"
# codegraph (issue #582) — a local SQLite symbol-graph MCP (search/explore/callers/callees/
# impact) run as a **stdio** server *inside* the container against the read-only `/repo` mount.
# Unlike Context7/GitHub (hosted HTTP, no runtime needed), codegraph is an npm CLI, so it needs
# Node baked into the image (see Dockerfile.codegraph) — hence it's only attached when a job
# actually has a `/repo` reference mount to index; there's nothing for it to graph otherwise.
# Targets the 412K-token/19min explore-and-report ceiling measured in CHAD_CODE_RESEARCH.md §1.
_CODEGRAPH_COMMAND = "codegraph"
_CODEGRAPH_ARGS = ["serve", "--mcp", "--path", "/repo", "--no-watch"]


def _cfg(key: str, default: Any) -> Any:
    return ConfigManager.get(f"tools.{key}", default)


def _looks_like_placeholder(val: Optional[str]) -> bool:
    """True if a secret value is obviously a fresh-config placeholder rather than a real token
    (e.g. ``your_..._here``). Guards against the ``secrets.yaml`` stub shadowing a real ``.env``
    value in ConfigManager.get_secret's (secrets-first) precedence."""
    if not val:
        return True
    low = str(val).strip().lower()
    return (len(low) < 25) or ("your" in low and "here" in low) or low.startswith(("<", "changeme"))


def _resolve_secret(key: str) -> Optional[str]:
    """Best real value for a secret: prefer ConfigManager.get_secret, but if that returns a
    placeholder (stale secrets.yaml stub), fall back to the raw environment (.env). See the
    GitHub-token shadowing bug — the real ghp_ token lives in .env while secrets.yaml has a stub."""
    val = ConfigManager.get_secret(key, None)
    if _looks_like_placeholder(val):
        env_val = os.environ.get(key)
        if not _looks_like_placeholder(env_val):
            return env_val
    return None if _looks_like_placeholder(val) else str(val)


def _build_mcp_config(enable_github: bool = False, has_reference: bool = False) -> Optional[Dict[str, Any]]:
    """Assemble the OpenHands `mcp_config` handed to the container coder, from Chad's config.

    Sources, merged:
      - ``tools.openhands_mcp_config`` — a raw ``{"mcpServers": {...}}`` passthrough (user-owned;
        add any HTTP/SSE MCP here).
      - ``tools.openhands_context7_mcp`` (bool, default True) — Context7 hosted docs MCP. Only 2
        tools, free/keyless — cheap to keep on for library/API doc lookups when debugging.
      - **GitHub MCP (44 tools) is CONDITIONAL, off by default.** Injecting 44 tool schemas on every
        job is heavy context the vast majority of coding tasks don't need. It's attached only when
        this job opts in (``enable_github``, set by Chad for git/PR tasks) OR the global
        ``tools.openhands_github_mcp`` config flag is True. Skipped (with a log) unless a real PAT is
        present. The github-workflow skill guards against unrequested destructive actions.
      - **codegraph MCP (issue #582) is gated on ``has_reference``**, i.e. this job actually mounted
        a codebase at ``/repo`` — a stdio server with nothing to index otherwise. Controlled by
        ``tools.openhands_codegraph_mcp`` (default True); it only *attaches* when both that flag is
        on AND ``has_reference`` is True. See the module docstring above the constants.

    Returns None when nothing is configured (the runner then runs with no MCP)."""
    servers: Dict[str, Any] = {}

    raw = _cfg("openhands_mcp_config", None)
    if isinstance(raw, dict):
        servers.update((raw.get("mcpServers") or {}))

    if bool(_cfg("openhands_context7_mcp", True)):
        servers["context7"] = {"url": _CONTEXT7_MCP_URL}

    if has_reference and bool(_cfg("openhands_codegraph_mcp", True)):
        servers["codegraph"] = {"command": _CODEGRAPH_COMMAND, "args": list(_CODEGRAPH_ARGS)}

    if enable_github or bool(_cfg("openhands_github_mcp", False)):
        token = _resolve_secret("GITHUB_PERSONAL_ACCESS_TOKEN")
        if token:
            # The hosted GitHub MCP authenticates via an explicit `Authorization: Bearer` header
            # (verified live — an `api_key` field 401s; the Bearer header loads all 44 tools).
            servers["github"] = {"url": _GITHUB_MCP_URL, "headers": {"Authorization": f"Bearer {token}"}}
        else:
            logger.info("[ OH ] github MCP requested but no valid GITHUB_PERSONAL_ACCESS_TOKEN "
                        "(placeholder/short in secrets.yaml and .env) — skipping GitHub MCP.")

    return {"mcpServers": servers} if servers else None


def _search_env() -> List[str]:
    """Docker ``-e`` args giving the container web-search access via the terminal (the base image
    has no npx, so the `web-search` skill curls the Brave REST API rather than running an MCP).
    Brave is the search backend of choice; the local SearXNG returns low-quality results and is
    intentionally not wired here."""
    env: List[str] = []
    brave = _resolve_secret("BRAVE_API_KEY")
    if brave:
        env += ["-e", f"BRAVE_API_KEY={brave}"]
    return env


def _repo_root() -> Path:
    # <root>/src/tools/dev/openhands_dispatch.py -> parents[3] == <root>
    return Path(__file__).resolve().parents[3]


def _runner_dir() -> Path:
    return _repo_root() / "docker" / "openhands"


def _workspace_root() -> Path:
    root = _cfg("chad_code_workspace_root", None)
    return Path(root) if root else Path(tempfile.gettempdir()) / "chad-code"


def _win_mount(path: Path) -> str:
    """Docker Desktop on Windows accepts ``C:/...`` bind sources but NOT ``/mnt/c/...``
    (that silently mounts an empty dir in the WSL distro). Normalize to forward slashes."""
    return str(path).replace("\\", "/")


def _container_name(job_id: str) -> str:
    return f"{_CONTAINER_PREFIX}{job_id}"


def _workspace_for(job_id: str) -> Path:
    return _workspace_root() / job_id


def _job_record(job_id: str) -> Path:
    return _workspace_root() / ".jobs" / f"{job_id}.path"


def _save_job_workspace(job_id: str, ws_path: Path) -> None:
    """Persist job_id -> host workspace path so check_coding_task finds the right dir even
    when the caller supplied a custom workspace (docker inspect only exposes the
    container-internal mount source, which isn't a host-walkable path on Windows)."""
    rec = _job_record(job_id)
    try:
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(str(ws_path), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — non-fatal bookkeeping
        logger.debug(f"[ OH ] could not record job workspace: {e}")


def _load_job_workspace(job_id: str) -> Path:
    """Host workspace path for a job; falls back to the default-layout path."""
    rec = _job_record(job_id)
    try:
        if rec.exists():
            return Path(rec.read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        pass
    return _workspace_for(job_id)


async def _run_docker(args: List[str], timeout: float = _DOCKER_TIMEOUT_S) -> tuple[int, str, str]:
    """Run a docker command (exec form — no shell, safe quoting). Returns (rc, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
    except FileNotFoundError:
        return 127, "", "docker executable not found on PATH"
    except asyncio.TimeoutError:
        return 124, "", f"docker command timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


async def _prune_old_job_containers() -> None:
    """Sweep stopped Chad Code job containers older than the TTL. Containers are kept (not
    ``--rm``) so ``check_coding_task`` can read logs after a run; this bounds that buildup.
    Idempotent, best-effort, and safe — the label + ``prune`` semantics only ever touch
    *stopped* ``chad-code`` containers, never a running job (issue #576)."""
    ttl_hours = _cfg("openhands_job_ttl_hours", _DEFAULT_JOB_TTL_HOURS)
    try:
        rc, out, err = await _run_docker(
            ["container", "prune", "-f",
             "--filter", f"label={_CONTAINER_LABEL}",
             "--filter", f"until={int(ttl_hours)}h"]
        )
        if rc == 0 and out.strip():
            logger.info(f"[ OH ] pruned old job containers: {out.strip().splitlines()[-1]}")
        elif rc != 0:
            logger.debug(f"[ OH ] container prune non-fatal error: {err.strip() or out.strip()}")
    except Exception as e:  # noqa: BLE001 — housekeeping must never block a dispatch
        logger.debug(f"[ OH ] container prune failed (non-fatal): {e}")


async def _ensure_coder_model(model: str) -> None:
    """Best-effort: make the coder model resident in LM Studio before dispatch. Chad's
    unique value-add — OpenHands can't manage local VRAM. Non-fatal (worst case the model
    JIT-loads on the agent's first call)."""
    try:
        from src.core.intelligence.model_lifecycle import ensure_model_loaded

        host_url = _cfg("openhands_lmstudio_host_url", _DEFAULT_HOST_LM_URL)
        ok = await ensure_model_loaded(host_url, model)
        logger.info(f"[ OH ] ensure coder model '{model}' resident: {ok}")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[ OH ] ensure_model_loaded failed (non-fatal): {e}")


async def _model_downloaded(host_url: str, model: str) -> Optional[bool]:
    """True/False if the coder model is downloaded in LM Studio, or None if we couldn't tell.
    Strips the LiteLLM ``openai/`` prefix before matching. Best-effort (never raises)."""
    bare = model.split("/", 1)[1] if model.startswith("openai/") else model
    base = host_url.rstrip("/").removesuffix("/v1")
    for path in ("/api/v0/models", "/v1/models"):
        try:
            import aiohttp

            async with aiohttp.ClientSession() as s:
                async with s.get(f"{base}{path}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        continue
                    data = (await r.json()).get("data", [])
                    return any(m.get("id") == bare for m in data)
        except Exception:  # noqa: BLE001 — try the next path / give up
            continue
    return None


async def preflight(model: Optional[str] = None, image: Optional[str] = None) -> Dict[str, Any]:
    """Check the harness prerequisites and return a structured, actionable report. Shared by
    ``dispatch_coding_task`` (fail fast with a clear error) and the GUI one-click setup button
    (issue #574). Each check is ``{ok, detail}``; ``ready`` is True only if all hard checks pass.

    ``image`` lets a caller check a specific image (e.g. the codegraph image for a
    reference-mounted job, issue #582) instead of the default base image.

    Hard checks (block a dispatch): docker daemon, image present.
    Soft checks (warn, don't block — the model JIT-loads / ensure step may still load it):
    LM Studio reachable, coder model downloaded."""
    model = model or _cfg("openhands_coder_model", _DEFAULT_MODEL)
    image = image or _cfg("openhands_image", _DEFAULT_IMAGE)
    host_url = _cfg("openhands_lmstudio_host_url", _DEFAULT_HOST_LM_URL)
    checks: Dict[str, Dict[str, Any]] = {}

    rc, _, err = await _run_docker(["info", "--format", "{{.ServerVersion}}"], timeout=15)
    checks["docker"] = {
        "ok": rc == 0,
        "detail": "Docker daemon reachable" if rc == 0
        else "Docker Desktop not running or docker not on PATH — start Docker Desktop.",
    }

    if checks["docker"]["ok"]:
        rc, _, _ = await _run_docker(["image", "inspect", image], timeout=15)
        # The codegraph image (#582) is built locally, not pullable — point at the build command
        # instead of a bogus `docker pull` for it.
        is_codegraph_image = image == _cfg("openhands_codegraph_image", _DEFAULT_CODEGRAPH_IMAGE)
        if rc == 0:
            missing_detail = f"Image present: {image}"
        elif is_codegraph_image:
            missing_detail = (f"Image missing — build it: docker build -f "
                              f"docker/openhands/Dockerfile.codegraph -t {image} docker/openhands")
        else:
            missing_detail = f"Image missing — pull it: docker pull {image}"
        checks["image"] = {"ok": rc == 0, "detail": missing_detail}
    else:
        checks["image"] = {"ok": False, "detail": "Skipped (docker down)."}

    downloaded = await _model_downloaded(host_url, model)
    if downloaded is None:
        checks["lmstudio"] = {"ok": False, "soft": True,
                              "detail": f"LM Studio not reachable at {host_url} — start it."}
        checks["model"] = {"ok": False, "soft": True,
                           "detail": f"Can't verify model '{model}' (LM Studio unreachable)."}
    else:
        checks["lmstudio"] = {"ok": True, "soft": True, "detail": f"LM Studio reachable at {host_url}"}
        checks["model"] = {
            "ok": bool(downloaded), "soft": True,
            "detail": f"Coder model downloaded: {model}" if downloaded
            else f"Coder model '{model}' not downloaded in LM Studio — download it.",
        }

    ready = all(c["ok"] for c in checks.values() if not c.get("soft"))
    return {"ready": ready, "model": model, "image": image, "checks": checks}


def _blocking_problems(report: Dict[str, Any]) -> List[str]:
    return [f"{k}: {c['detail']}" for k, c in report["checks"].items()
            if not c.get("soft") and not c["ok"]]


# Per-process allowlist additions, set by eval_clone_provisioner.provision_eval_clone (#596)
# for the SHA-pinned clone it just created — avoids permanently editing config.yaml's
# openhands_workspace_allowlist (which stays [] by default; epic non-goal is 'monorepo not
# writable by default', #576) for a throwaway self-eval clone. Public (no leading underscore):
# it's a real cross-module contract with eval_clone_provisioner, not an internal.
EXTRA_ALLOWLIST_ENV = "CHAD_CODE_EXTRA_ALLOWLIST"


def _workspace_guard(ws_path: Path) -> Optional[str]:
    """Refuse a caller-supplied workspace that lives inside a git repo (e.g. the Chad monorepo)
    unless it's under a configured allowlist root — the epic non-goal is 'monorepo not writable
    by default' (#576). Returns an error string if blocked, else None."""
    resolved = ws_path.resolve()
    allow = [Path(p).resolve() for p in (_cfg("openhands_workspace_allowlist", []) or [])]
    extra = os.environ.get(EXTRA_ALLOWLIST_ENV, "")
    allow += [Path(p).resolve() for p in extra.split(os.pathsep) if p.strip()]
    if any(resolved == a or a in resolved.parents for a in allow):
        return None
    for parent in (resolved, *resolved.parents):
        if (parent / ".git").exists():
            return (f"workspace '{resolved}' is inside a git repo ({parent}); refusing to let a "
                    f"coding job write into a real repo. Use a fresh workspace, or add a root to "
                    f"tools.openhands_workspace_allowlist to permit it.")
    return None


class OpenHandsDispatchTool(BaseTool):
    """Dispatch a coding task to a headless OpenHands container running a local model."""

    name = "dispatch_coding_task"
    description = (
        "Delegate a real coding task (build/edit/test files in a workspace) to the OpenHands "
        "coding harness running a local model, as a background job. Returns a job id to check "
        "later with check_coding_task. Use for actual software work, not quick code snippets."
    )
    category = "dev"
    is_side_effecting = True
    requires_approval = True
    approval_prompt = "Dispatch a coding task to OpenHands (runs a local model on files): {task}"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "The coding task as a clear, self-contained brief (what to "
                            "build/change, constraints, how to verify)."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label for the job (optional).",
                    },
                    "workspace": {
                        "type": "string",
                        "description": (
                            "Optional existing host directory to work in. If omitted, a fresh "
                            "empty workspace is created for the job."
                        ),
                    },
                    "reference": {
                        "type": "string",
                        "description": (
                            "Optional host directory to mount READ-ONLY at /repo — a codebase to "
                            "explore/reference without modifying. The job writes output to the "
                            "separate writable /workspace."
                        ),
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Max agent iterations before it stops (default 30).",
                    },
                    "enable_github": {
                        "type": "boolean",
                        "description": (
                            "Set True ONLY for tasks that need GitHub (read issues/PRs, open a PR, "
                            "search a repo's code). Adds the GitHub toolset. Leave False/omitted for "
                            "ordinary coding — it keeps the coder focused and the context lean."
                        ),
                    },
                },
                "required": ["task"],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        label = (params or {}).get("title") or (params or {}).get("task", "")
        label = label if len(label) <= 60 else label[:57] + "..."
        return f'Dispatching coding task: "{label}"' if label else "Dispatching coding task"

    def validate_params(self, task: Optional[str] = None, **kwargs: Any) -> tuple[bool, str | None]:
        if not task or not isinstance(task, str) or not task.strip():
            return False, "task must be a non-empty string."
        runner = _runner_dir() / "chad_headless_runner.py"
        if not runner.exists():
            return False, f"headless runner not found at {runner}"
        return True, None

    async def execute(
        self,
        task: str,
        title: Optional[str] = None,
        workspace: Optional[str] = None,
        reference: Optional[str] = None,
        max_iterations: Optional[int] = None,
        enable_github: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # codegraph (issue #582) is only useful when a reference repo is mounted at /repo — gate
        # both the MCP entry and which image to launch on that (default flag ON; it just won't
        # attach without a reference). Ordinary jobs with no reference keep the lighter base image.
        has_reference = bool(reference)
        use_codegraph = has_reference and bool(_cfg("openhands_codegraph_mcp", True))
        image = (_cfg("openhands_codegraph_image", _DEFAULT_CODEGRAPH_IMAGE) if use_codegraph
                 else _cfg("openhands_image", _DEFAULT_IMAGE))
        model = _cfg("openhands_coder_model", _DEFAULT_MODEL)
        container_lm_url = _cfg("openhands_container_lmstudio_url", _DEFAULT_CONTAINER_LM_URL)
        api_key = _cfg("openhands_lmstudio_api_key", "lmstudio")
        max_iter = int(max_iterations or _cfg("openhands_max_iterations", _DEFAULT_MAX_ITER))
        native_by_model = _cfg("openhands_native_tool_calling_by_model", {}) or {}
        native_tc = bool(native_by_model.get(
            model, _cfg("openhands_native_tool_calling", _DEFAULT_NATIVE_TOOL_CALLING)))
        use_tracker = bool(_cfg("openhands_use_task_tracker", False))
        # Both default OFF pending an A/B against the existing eval tiers (issue #358).
        use_subagents = bool(_cfg("openhands_use_subagents", False))
        use_condenser = bool(_cfg("openhands_use_condenser", False))
        condenser_max_size = int(_cfg("openhands_condenser_max_size", 240))
        condenser_keep_first = int(_cfg("openhands_condenser_keep_first", 2))
        # Production critic (EmptyPatchCritic) for ordinary jobs — distinct from the dev-only
        # eval critic below. OFF pending an A/B (issue #584).
        use_critic = bool(_cfg("openhands_use_critic", False))
        # Eval critic (issue #358) — DEV-ONLY, and deliberately NOT a dispatch_coding_task
        # parameter: it would add noise to the tool schema Chad sees for a capability no user
        # has. The autonomous loop driver sets these on the Brain process before dispatching.
        # Empty suite (the default) means no critic and no behaviour change.
        eval_suite = (os.environ.get("CHAD_EVAL_SUITE", "") or "").strip()
        eval_case = (os.environ.get("CHAD_EVAL_CASE", "") or "").strip() or None
        eval_bridge_url = (os.environ.get("CHAD_EVAL_BRIDGE_URL", "")
                           or "http://host.docker.internal:8799").strip()
        try:
            eval_max_attempts = int(os.environ.get("CHAD_EVAL_MAX_ATTEMPTS", "3"))
        except ValueError:
            eval_max_attempts = 3
        timeout_s = int(_cfg("openhands_job_timeout_s", _DEFAULT_JOB_TIMEOUT_S))
        llm_timeout_s = max(1, int(_cfg("openhands_llm_timeout_s", 900)))
        stream_events = bool(_cfg("openhands_stream_events", True))  # live CHAD_EVENT lines (#575)
        mcp_config = _build_mcp_config(enable_github=bool(enable_github),  # github only if opted in
                                        has_reference=has_reference)

        # Preflight: fail fast with an actionable error instead of a raw docker failure (#574).
        report = await preflight(model, image=image)
        problems = _blocking_problems(report)
        if problems:
            return {"success": False, "error": "Chad Code prerequisites not met — "
                    + "; ".join(problems), "preflight": report}

        # Housekeeping: sweep old stopped job containers before starting a new one (#576).
        await _prune_old_job_containers()

        job_id = uuid.uuid4().hex[:8]
        container = _container_name(job_id)

        # Prepare the workspace dir (fresh empty one unless the caller supplied a path).
        if workspace:
            ws_path = Path(workspace)
            if not ws_path.is_dir():
                return {"success": False, "error": f"workspace is not an existing directory: {workspace}"}
            guard = _workspace_guard(ws_path)  # don't let a job clobber a real repo (#576)
            if guard:
                return {"success": False, "error": guard}
        else:
            ws_path = _workspace_for(job_id)
            ws_path.mkdir(parents=True, exist_ok=True)

        # Optional reference codebase for explore-and-report tasks. The host dir is ALWAYS mounted
        # read-only so it can't be clobbered. Without codegraph it's mounted straight at /repo.
        # With codegraph on, it's mounted at /repo_src:ro and the entrypoint copies it to a writable
        # /repo and builds the codegraph index there — codegraph needs a writable `.codegraph/` dir
        # (verified live #582: `serve` errors "not initialized" on a bare read-only mount), and the
        # copy keeps the host repo untouched while giving codegraph somewhere to write.
        ref_mount: List[str] = []
        if reference:
            ref_path = Path(reference)
            if not ref_path.is_dir():
                return {"success": False, "error": f"reference is not an existing directory: {reference}"}
            ref_target = "/repo_src" if use_codegraph else "/repo"
            ref_mount = ["-v", f"{_win_mount(ref_path)}:{ref_target}:ro"]

        # Container entrypoint. With codegraph on, first materialize a writable /repo from the
        # read-only /repo_src mount and build the codegraph index into it before starting the agent
        # (index build is ~5s per ~700 files). Both steps are non-fatal — a copy/index failure just
        # means the agent falls back to terminal exploration (the graceful MCP fallback in the
        # runner handles a codegraph server that then can't answer). The CHAD_CODEGRAPH_* markers
        # land in `docker logs` for the eval forensics.
        if use_codegraph:
            entrypoint = (
                "mkdir -p /repo && cp -a /repo_src/. /repo/ 2>/dev/null; "
                "(codegraph init /repo > /tmp/cg_init.log 2>&1 "
                "&& echo CHAD_CODEGRAPH_INDEXED || echo CHAD_CODEGRAPH_INIT_FAILED); "
                "cd /workspace && python /chad/chad_headless_runner.py"
            )
        else:
            entrypoint = "cd /workspace && python /chad/chad_headless_runner.py"

        # Ensure the coder model is resident (best-effort, Chad's value-add).
        await _ensure_coder_model(model)

        # Launch a detached container running the headless runner. Env is passed in exec
        # form (no shell) so arbitrary/multiline task text needs no escaping.
        args = [
            "run", "-d", "--name", container,
            "--label", f"{_CONTAINER_LABEL}=1",
            "--label", f"{_CONTAINER_LABEL}-job={job_id}",
            "-v", f"{_win_mount(ws_path)}:/workspace",
            "-v", f"{_win_mount(_runner_dir())}:/chad:ro",
            *ref_mount,
            "-e", f"CHAD_TASK={task}",
            "-e", f"CHAD_MODEL={model}",
            "-e", f"CHAD_LLM_BASE_URL={container_lm_url}",
            "-e", f"CHAD_LLM_API_KEY={api_key}",
            "-e", f"CHAD_MAX_ITER={max_iter}",
            "-e", f"CHAD_NATIVE_TOOL_CALLING={'1' if native_tc else '0'}",
            "-e", f"CHAD_USE_TASK_TRACKER={'1' if use_tracker else '0'}",
            "-e", f"CHAD_USE_SUBAGENTS={'1' if use_subagents else '0'}",
            "-e", f"CHAD_USE_CONDENSER={'1' if use_condenser else '0'}",
            "-e", f"CHAD_CONDENSER_MAX_SIZE={condenser_max_size}",
            "-e", f"CHAD_CONDENSER_KEEP_FIRST={condenser_keep_first}",
            "-e", f"CHAD_USE_CRITIC={'1' if use_critic else '0'}",
            *(["-e", f"CHAD_EVAL_SUITE={eval_suite}",
               "-e", f"CHAD_EVAL_CASE={eval_case or ''}",
               "-e", f"CHAD_EVAL_BRIDGE_URL={eval_bridge_url}",
               "-e", f"CHAD_EVAL_MAX_ATTEMPTS={eval_max_attempts}",
               # HOST path of the workspace, so the bridge runs the eval against the code the
               # coder edited rather than the host checkout.
               "-e", f"CHAD_EVAL_WORKSPACE={ws_path}"] if eval_suite else []),
            "-e", f"CHAD_TIMEOUT_S={timeout_s}",
            "-e", f"CHAD_LLM_TIMEOUT_S={llm_timeout_s}",
            "-e", f"CHAD_STREAM_EVENTS={'1' if stream_events else '0'}",
            # Git identity. Without it any `git commit` in the sandbox dies with "Author
            # identity unknown ... unable to auto-detect email address", and the agent burns
            # iterations retrying it (observed live on the first self-eval probe run). The
            # github-workflow skill actively encourages small commits, so this is not exotic.
            "-e", "GIT_AUTHOR_NAME=Chad Code",
            "-e", "GIT_AUTHOR_EMAIL=chad-code@localhost",
            "-e", "GIT_COMMITTER_NAME=Chad Code",
            "-e", "GIT_COMMITTER_EMAIL=chad-code@localhost",
            *(["-e", f"CHAD_MCP_CONFIG={json.dumps(mcp_config)}"] if mcp_config else []),
            *_search_env(),
            "--add-host", "host.docker.internal:host-gateway",
            "--entrypoint", "bash",
            image,
            "-c", entrypoint,
        ]
        rc, out, err = await _run_docker(args)
        if rc != 0:
            return {
                "success": False,
                "error": f"Failed to launch OpenHands container: {err.strip() or out.strip()}",
            }

        _save_job_workspace(job_id, ws_path)
        result = {
            "job_id": job_id,
            "container": container,
            "workspace": str(ws_path),
            "model": model,
            "status": "running",
        }
        return {
            "success": True,
            "result": result,
            "summary": (
                f"Dispatched coding job {job_id} (model {model}) to OpenHands. "
                f"Working in {ws_path}. Check progress with check_coding_task(job_id='{job_id}')."
            ),
        }


class CheckCodingTaskTool(BaseTool):
    """Report a dispatched coding job's status, transcript tail, and workspace files."""

    name = "check_coding_task"
    description = (
        "Check a coding job previously dispatched with dispatch_coding_task: its status "
        "(running/finished/failed), the latest agent transcript, and the files in its workspace."
    )
    category = "dev"
    is_side_effecting = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The job_id returned by dispatch_coding_task.",
                    },
                },
                "required": ["job_id"],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        jid = (params or {}).get("job_id", "")
        return f"Checking coding job {jid}" if jid else "Checking coding job"

    def validate_params(self, job_id: Optional[str] = None, **kwargs: Any) -> tuple[bool, str | None]:
        if not job_id or not str(job_id).strip():
            return False, "job_id must be a non-empty string."
        return True, None

    async def execute(self, job_id: str, **kwargs: Any) -> Dict[str, Any]:
        container = _container_name(job_id)

        rc, out, _ = await _run_docker(
            ["inspect", container, "--format", "{{.State.Status}}|{{.State.ExitCode}}"]
        )
        if rc != 0:
            return {"success": False, "error": f"No such coding job: {job_id}"}
        state, _, exit_code = out.strip().partition("|")

        _, logs, _ = await _run_docker(["logs", "--tail", "40", container])
        transcript = self._clean_transcript(logs)
        done = _DONE_SENTINEL in logs
        failed = _ERR_SENTINEL in logs

        if state == "running":
            status = "running"
        elif done and not failed and exit_code == "0":
            status = "finished"
        else:
            status = "failed"

        ws = _load_job_workspace(job_id)
        files = self._list_workspace(ws)
        return {
            "success": True,
            "result": {
                "job_id": job_id,
                "status": status,
                "exit_code": exit_code,
                "workspace": str(ws),
                "files": files,
                "transcript_tail": transcript,
            },
            "summary": f"Coding job {job_id}: {status} ({len(files)} file(s) in workspace).",
        }

    @staticmethod
    def _clean_transcript(logs: str, limit: int = 25) -> List[str]:
        noise = ("OpenHands SDK v", "Report a bug", "Get help", "Scale up", "joinslack",
                 "Set OPENHANDS", "+---", "|  ", "Cost calculation", "warnings.warn")
        lines = [ln.rstrip() for ln in logs.splitlines()
                 if ln.strip() and not any(n in ln for n in noise)]
        return lines[-limit:]

    @staticmethod
    def _list_workspace(ws: Path, limit: int = 50) -> List[str]:
        if not ws.exists():
            return []
        out: List[str] = []
        for root, _dirs, names in os.walk(ws):
            for n in names:
                rel = os.path.relpath(os.path.join(root, n), ws).replace("\\", "/")
                out.append(rel)
                if len(out) >= limit:
                    return out
        return out


class StopCodingTaskTool(BaseTool):
    """Stop a running dispatched coding job (``docker stop`` its container). Its workspace files
    are left intact and the stopped container is still inspectable with check_coding_task."""

    name = "stop_coding_task"
    description = (
        "Stop a running coding job previously started with dispatch_coding_task (e.g. it's "
        "stuck, looping, or no longer needed). The container is stopped; workspace files remain."
    )
    category = "dev"
    is_side_effecting = True

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job_id to stop."},
                },
                "required": ["job_id"],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        jid = (params or {}).get("job_id", "")
        return f"Stopping coding job {jid}" if jid else "Stopping coding job"

    def validate_params(self, job_id: Optional[str] = None, **kwargs: Any) -> tuple[bool, str | None]:
        if not job_id or not str(job_id).strip():
            return False, "job_id must be a non-empty string."
        return True, None

    async def execute(self, job_id: str, **kwargs: Any) -> Dict[str, Any]:
        container = _container_name(job_id)
        rc, out, err = await _run_docker(["stop", "-t", "5", container])
        if rc != 0:
            detail = err.strip() or out.strip()
            not_running = "is not running" in detail or "No such container" in detail
            return {"success": not_running, "result": {"job_id": job_id, "stopped": False},
                    "summary": f"Coding job {job_id}: {detail or 'could not stop'}."}
        return {"success": True, "result": {"job_id": job_id, "stopped": True},
                "summary": f"Stopped coding job {job_id}."}
