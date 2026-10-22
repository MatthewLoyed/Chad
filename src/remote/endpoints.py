"""
Remote REST and Webhook Endpoints
Contains explicit logic for handling HTTP paths and generating standard Starlette route lists.
"""

import logging
import asyncio
import base64
import os
import queue as thread_queue
import re
from pathlib import Path

import httpx
from starlette.responses import Response, JSONResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute, Mount
from starlette.staticfiles import StaticFiles

from src.shared.logger import setup_logger
from src.remote.webhooks.webhook_manager import get_webhook_manager
from src.shared.file_registry import file_registry
from src.application.events import TurnFeedbackEvent

logger = setup_logger("Endpoints")


def _is_loopback_request(request) -> bool:
    """True when the caller is on this same machine (see auth.is_loopback_host)."""
    from src.remote.auth import is_loopback_host

    host = request.client.host if request.client else None
    return is_loopback_host(host)


async def _buddy_state_endpoint(request):
    """Return current desktop buddy snapshot (debug / initial poll)."""
    server = request.app.state.server
    engine = getattr(server, "buddy_engine", None) or getattr(server, "buddy_bridge", None)
    if engine is None:
        return JSONResponse({"state": "idle", "mode": "idle", "active_tools": 0, "turn_active": False})
    return JSONResponse(engine.get_state())


async def _buddy_stream_endpoint(request):
    """SSE stream — pushes buddy state only when it changes."""
    from src.buddy import BuddyEngine

    server = request.app.state.server
    engine = getattr(server, "buddy_engine", None) or getattr(server, "buddy_bridge", None)
    if engine is None:
        async def empty():
            yield BuddyEngine.sse_line({"state": "idle", "mode": "idle", "active_tools": 0, "turn_active": False})

        return StreamingResponse(empty(), media_type="text/event-stream")

    updates: thread_queue.Queue = thread_queue.Queue()

    def on_state(payload: dict) -> None:
        updates.put(payload)

    unsubscribe = engine.subscribe(on_state)

    async def event_generator():
        try:
            yield BuddyEngine.sse_line(engine.get_state())
            while True:
                payload = await asyncio.to_thread(updates.get)
                yield BuddyEngine.sse_line(payload)
        finally:
            unsubscribe()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _health_endpoint(request):
    """Render (and load balancers) send GET/HEAD to this path; return 200 so checks pass."""
    logger.info("[ HEALTH ] : Probe received")
    return Response(status_code=200, content=b"ok", media_type="text/plain")


async def _webhook_gmail_endpoint(request):
    """Handle Gmail Pub/Sub push notifications."""
    try:
        payload = await request.json()
        headers = dict(request.headers)
        
        # Security: Extract token from query params if present
        token = request.query_params.get("token")
        if token:
            headers["x-chad-token"] = token

        webhook_manager = get_webhook_manager()
        # Verify=True now that we support token-based authentication
        event = await webhook_manager.process_webhook("gmail", payload, headers, verify=True)
        if event == "REPLAY":
            return JSONResponse({"status": "acknowledged"}, status_code=200)

        if event:
            return JSONResponse({"status": "success"})
        else:
            return JSONResponse({"status": "rejected"}, status_code=400)
    except Exception as e:
        logger.error(f"Gmail webhook error: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


async def _webhook_telegram_endpoint(request):
    """Handle Telegram bot webhooks."""
    try:
        payload = await request.json()
        headers = dict(request.headers)

        webhook_manager = get_webhook_manager()
        # process_webhook now correctly triggers a Turn in the orchestrator
        event = await webhook_manager.process_webhook("telegram", payload, headers)

        if event:
            return JSONResponse({"status": "ok"})
        else:
            return JSONResponse({"status": "rejected"}, status_code=400)
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


async def _webhook_github_endpoint(request):
    """Handle GitHub webhooks."""
    try:
        payload = await request.json()
        headers = dict(request.headers)

        webhook_manager = get_webhook_manager()
        event = await webhook_manager.process_webhook("github", payload, headers)

        if event:
            return JSONResponse({"status": "success"})
        else:
            return JSONResponse({"status": "rejected"}, status_code=400)
    except Exception as e:
        logger.error(f"GitHub webhook error: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


async def _trigger_heartbeat_endpoint(request):
    """Manually trigger the heartbeat service."""
    server = getattr(request.app.state, "server", None)
    if server and server._heartbeat_service:
        # Execute in background to avoid blocking health check/web request
        asyncio.create_task(server._heartbeat_service._execute_heartbeat())
        return JSONResponse({"status": "heartbeat_triggered"})
    return JSONResponse({"status": "error", "message": "Heartbeat service not initialized"}, status_code=500)


async def _download_file_endpoint(request):
    """Serve a file artifact from the registry via token."""
    logger.warning(f"[ DOWNLOAD HIT ] token={request.path_params.get('token')}, origin={request.headers.get('origin')}")
    token = request.path_params.get("token")
    if not token:
        return JSONResponse({"error": "Missing token"}, status_code=400)
        
    file_info = file_registry.get_file(token)
    if not file_info:
        logger.error(f"[ DOWNLOAD ] : Token {token} not found in registry.")
        return Response(status_code=404, content=b"File not found or expired.")
        
    try:
        # Decode base64 data
        data = base64.b64decode(file_info['data'])
        filename = file_info.get('name', 'downloaded_file')
        mime = file_info.get('mime', 'application/octet-stream')
        
        # Determine if we should serve inline (for previews) or attachment (for downloads)
        is_html = mime == 'text/html'
        disposition = 'inline' if is_html else 'attachment'
        
        logger.warning(f"[ DOWNLOAD ] : Serving {filename} ({len(data)} bytes) for token {token} (mode={disposition})")
        
        return Response(
            content=data,
            media_type=mime,
            headers={"Content-Disposition": f"{disposition}; filename=\"{filename}\""}
        )
    except Exception as e:
        logger.error(f"Download error for token {token}: {e}")
        return JSONResponse({"error": "Internal server error during download"}, status_code=500)


async def _list_artifacts_endpoint(request):
    """List all permanently stored artifacts."""
    try:
        artifacts = file_registry.list_artifacts()
        return JSONResponse({"artifacts": artifacts})
    except Exception as e:
        logger.error(f"Failed to list artifacts: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def _execute_command_endpoint(request):
    """Relay incoming Application Command request to Server CommandHandler."""
    from src.shared.config_manager import ConfigManager
    # Auth Guard. Loopback callers (the paired desktop GUI) are always
    # allowed — same policy as _check_update_token. Non-loopback callers must
    # present the token; ChadServer generates+persists one on first boot if
    # none is configured (see server.py), so this fails closed rather than
    # allowing unauthenticated remote command execution.
    server_token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
    if not _is_loopback_request(request):
        provided_token = request.headers.get("x-chad-token") or request.query_params.get("token")
        if not server_token or provided_token != server_token:
            client_host = request.client.host if request.client else 'Unknown'
            logger.warning(f"[ COMMAND ] : Unauthorized command execution attempt from IP={client_host}")
            return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        payload = await request.json()
        cmd_class_name = payload.get("command_class")
        cmd_args = payload.get("command_args", {})
        
        if not cmd_class_name:
            return JSONResponse({"success": False, "error": "Missing command_class"}, status_code=400)

        # Dynamically instantiate registered application Command
        import src.application.commands as cmd_module
        cls = getattr(cmd_module, cmd_class_name, None)
        
        from src.application.commands.base_command import Command
        if not cls or not issubclass(cls, Command):
            logger.error(f"[ COMMAND ] : Unknown command class requested: {cmd_class_name}")
            return JSONResponse({"success": False, "error": f"Unknown command: {cmd_class_name}"}, status_code=400)
            
        # Re-hydrate dataclass safely
        command = cls(**cmd_args)
        
        # Fetch Handler & Run from stored server state
        server = getattr(request.app.state, "server", None)
        if not server or not server.components:
             logger.error("[ COMMAND ] : Server instance or components not yet ready.")
             return JSONResponse({"success": False, "error": "Server components initializing"}, status_code=503)
             
        handler = server.components.get("command_handler")
        if not handler:
             logger.error("[ COMMAND ] : CommandHandler component missing from initialized stack.")
             return JSONResponse({"success": False, "error": "CommandHandler missing"}, status_code=503)

        logger.info(f"[ COMMAND ] : Re-routing {cmd_class_name} on Server stack...")
        result = await handler.execute(command)
        return JSONResponse({"success": True, "result": result})
        
    except Exception as e:
        logger.error(f"Remote command execution failed: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


def _run_git(args, cwd, timeout=120):
    """Run a git/shell command, return (returncode, stdout, stderr)."""
    import subprocess
    import shutil

    cmd = list(args)
    if cmd and cmd[0] == "git":
        git = shutil.which("git")
        if not git:
            raise FileNotFoundError(
                "git not found on PATH — install Git for Windows or add it to PATH"
            )
        cmd[0] = git
    proc = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


_BRANCH_NAME_RE = re.compile(r"^[\w./-]+$")


def _validate_branch_name(branch: str) -> bool:
    return bool(
        branch
        and _BRANCH_NAME_RE.fullmatch(branch)
        and not branch.startswith("-")
        and ".." not in branch
    )


def _is_dev_brain() -> bool:
    """True when this Brain serves the Chad Dev stack (port 8788), not prod."""
    return os.environ.get("PORT", "8767") != "8767"


def _branch_switch_blocked(branch: str) -> str | None:
    """Return an error message when a branch update/switch is not allowed."""
    if not _validate_branch_name(branch):
        return f"Invalid branch name: {branch}"
    if not _is_dev_brain() and branch != "master":
        return "Branch switching is only available on the dev stack (port 8788)."
    return None


# Runtime stamps — auto-rewritten by builds/launches; never block branch switch.
_DIRTY_GUARD_IGNORE = frozenset({
    "data/dev_logs/dev_stack.pids",
    "data/ui_build_version.json",
    "data/ui_loaded_version.json",
})


def _meaningful_dirty_paths(porcelain: str) -> list[str]:
    """Porcelain lines that should block git reset --hard (exclude runtime stamps)."""
    paths: list[str] = []
    for raw in porcelain.splitlines():
        line = raw.rstrip("\r\n")
        if len(line) < 4:
            continue
        # Porcelain: XY + space + path (see git status --porcelain)
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        if path.replace("\\", "/") not in _DIRTY_GUARD_IGNORE:
            paths.append(path)
    return paths


def _parse_remote_branches(raw: str) -> list[str]:
    branches: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line in {"origin", "origin/HEAD"}:
            continue
        if line.startswith("origin/"):
            name = line[len("origin/") :]
            if name == "HEAD":
                continue
            branches.append(name)
    return sorted(set(branches))


def get_version_info() -> dict:
    """Deployed vs running version snapshot for Settings and /api/version.

    ``sha`` (legacy) is the SHA the Brain process loaded at boot, not disk HEAD.
    Use ``deployed.sha`` vs ``running.sha`` to detect a stale server without restart.
    """
    from src.shared.runtime_identity import get_version_snapshot

    try:
        return get_version_snapshot(Path(__file__).parents[2])
    except Exception as e:
        logger.warning(f"[ VERSION ] : Could not build version snapshot: {e}")
        return {
            "sha": "unknown",
            "deployed_sha": "unknown",
            "branch": None,
            "date": None,
            "needs_restart": True,
        }


def _schedule_brain_restart(server) -> None:
    """Graceful uvicorn shutdown; prod supervisor relaunches with fresh imports."""

    async def _delayed_restart():
        await asyncio.sleep(1.5)
        if server and hasattr(server, "request_restart"):
            server.request_restart()
        else:
            logger.error("[ UPDATE ] : No server reference — cannot schedule Brain restart.")

    asyncio.create_task(_delayed_restart())


async def _version_endpoint(request):
    """Public, unauthenticated version probe (no secrets — just SHA/date/branch),
    so the web client can show 'what version am I running' without a token."""
    return JSONResponse(get_version_info())


async def _ping_http(url: str, timeout: float = 3.0) -> bool:
    """Cheap liveness GET — True on any non-5xx response (service is at least answering)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except Exception:
        return False


async def _check_brain(request) -> dict:
    # We are the Brain answering this request — reaching this handler proves liveness.
    return {"id": "brain", "label": "Brain", "tier": "core", "healthy": True, "detail": None}


async def _check_lm_studio() -> dict:
    from src.shared.config_manager import ConfigManager

    base_url = ConfigManager.get("models.local.instances.lmstudio.base_url", "http://localhost:1234/v1")
    healthy = await _ping_http(f"{base_url.rstrip('/')}/models")
    return {
        "id": "lm_studio",
        "label": "LM Studio",
        "tier": "core",
        "healthy": healthy,
        "detail": None if healthy else f"No response from {base_url} (only required for local-model mode).",
    }


async def _check_searxng() -> dict:
    from src.shared.config_manager import ConfigManager

    url = str(ConfigManager.get("tools.searxng_url", "http://127.0.0.1:8080")).rstrip("/")
    healthy = await _ping_http(url)
    detail = None
    if healthy:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{url}/search",
                    params={"q": "healthcheck", "format": "json"},
                )
                resp.raise_for_status()
                body = resp.json()
                if not isinstance(body, dict) or "results" not in body:
                    healthy = False
                    detail = "SearXNG responded but JSON search is broken."
        except Exception:
            healthy = False
            detail = "SearXNG UI is up but /search JSON is unreachable."
    else:
        detail = "Falling back to Playwright/Brave for web search."
    return {
        "id": "searxng",
        "label": "SearXNG",
        "tier": "enhanced",
        "healthy": healthy,
        "detail": detail,
    }


async def _check_kroki() -> dict:
    from src.shared.config_manager import ConfigManager

    url = ConfigManager.get("visual.kroki_url", "https://kroki.io")
    healthy = await _ping_http(url)
    return {
        "id": "kroki",
        "label": "Kroki",
        "tier": "enhanced",
        "healthy": healthy,
        "detail": None if healthy else f"No response from {url}; diagram rendering will fail.",
    }


async def _check_presenton() -> dict:
    from src.shared.config_manager import ConfigManager
    from src.infrastructure.services.presenton_sidecar import _presenton_connected

    url = ConfigManager.get("visual.presenton_url")
    if not url:
        return {
            "id": "presenton",
            "label": "Presenton",
            "tier": "optional",
            "healthy": False,
            "detail": "Not configured (set visual.presenton_url to enable generate_presentation).",
        }
    base_url = str(url).rstrip("/")
    reachable = await _ping_http(base_url)
    healthy = await _presenton_connected(base_url) if reachable else False
    detail = None
    if not reachable:
        detail = f"No response from {base_url}; AI deck generation unavailable."
    elif not healthy:
        detail = "Presenton is reachable, but Chad's admin credentials were not accepted."
    return {
        "id": "presenton",
        "label": "Presenton",
        "tier": "optional",
        "healthy": healthy,
        "detail": None if healthy else detail,
    }


def _check_typst() -> dict:
    import shutil

    from src.shared.config_manager import ConfigManager

    typst_bin = ConfigManager.get("visual.typst_bin", "typst")
    healthy = os.path.isfile(typst_bin) if typst_bin != "typst" else bool(shutil.which("typst"))
    return {
        "id": "typst",
        "label": "Typst CLI",
        "tier": "enhanced",
        "healthy": healthy,
        "detail": None if healthy else "Install: winget install Typst.Typst",
    }


def _check_marp() -> dict:
    import shutil

    from src.shared.config_manager import ConfigManager

    marp_command = ConfigManager.get("visual.marp_command", None)
    if marp_command:
        binary = marp_command[0] if isinstance(marp_command, list) else str(marp_command).split()[0]
        healthy = bool(shutil.which(binary))
    else:
        # Defaults to `npx --yes @marp-team/marp-cli` — only needs npx on PATH.
        healthy = bool(shutil.which("npx"))
    return {
        "id": "marp",
        "label": "Marp CLI",
        "tier": "enhanced",
        "healthy": healthy,
        "detail": None if healthy else "Install Node.js (provides npx) or set visual.marp_command.",
    }


def _check_local_first_subsystems() -> list:
    """Local-vs-cloud storage mode for each local-first subsystem (issue #472).
    Static capability flags, not liveness probes — `healthy` is always True since
    there's nothing to reach over the network; `mode` is what actually varies."""
    from src.infrastructure.database.supabase_manager import get_supabase_manager

    local = get_supabase_manager().enabled is False
    subsystem_mode = "local" if local else "cloud"
    subsystem_detail = None if local else "Supabase configured — routed to cloud Postgres."

    def _entry(id_, label):
        return {"id": id_, "label": label, "tier": "core", "healthy": True, "detail": subsystem_detail, "mode": subsystem_mode}

    return [
        _entry("cron_tasks", "Cron / Agent Tasks"),
        _entry("reflection_lessons", "Reflection Lessons"),
        _entry("kanban", "Kanban Board"),
        _entry("workflows", "Workflows"),
        _entry("dashboard_feed", "Dashboard Feed"),
        _entry("oats", "OATS Tool Refinement"),
        {
            "id": "telegram", "label": "Telegram", "tier": "optional", "healthy": True,
            "mode": "local" if local else "cloud",
            "detail": "Routing/session state is local; the Telegram Bot API transport is inherently cloud-hosted.",
        },
        {
            "id": "multi_user_auth", "label": "Multi-User Auth", "tier": "optional", "healthy": True,
            "mode": "cloud",
            "detail": "Requires Supabase; not localized." if local else None,
        },
    ]


async def _check_openverse() -> dict:
    """Openverse powers search_images with no API key — surface it on Services."""
    url = "https://api.openverse.org/v1/images/"
    healthy = False
    detail = None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                url,
                params={"q": "healthcheck", "page_size": 1},
                headers={"User-Agent": "ChadAI/1.0 (services health)"},
            )
            resp.raise_for_status()
            body = resp.json()
            healthy = isinstance(body, dict) and "results" in body
            if not healthy:
                detail = "Openverse responded but image search JSON looks wrong."
    except Exception as exc:
        detail = f"Openverse unreachable: {exc}"
    return {
        "id": "openverse",
        "label": "Openverse (image search)",
        "healthy": healthy,
        "url": url,
        "detail": detail,
        "hint": (
            "Used by search_images for Creative Commons photos/art — no API key. "
            "Optional stock keys (Pexels/Unsplash/Pixabay) live under Connectors."
        ),
    }


async def _services_health_endpoint(request):
    """Aggregate health for Chad's infrastructure dependencies (issue #363).
    Unauthenticated read-only diagnostic — no secrets in the response, mirrors /api/version."""
    brain, lm_studio, searxng, kroki, presenton, openverse = await asyncio.gather(
        _check_brain(request),
        _check_lm_studio(),
        _check_searxng(),
        _check_kroki(),
        _check_presenton(),
        _check_openverse(),
    )
    services = [brain, lm_studio, searxng, openverse, kroki, presenton, _check_typst(), _check_marp()]
    services.extend(_check_local_first_subsystems())
    return JSONResponse({"services": services})


async def perform_self_update(server=None, branch: str = "master", *, force: bool = False) -> dict:
    """Pull origin/<branch> when behind, rebuild stale UI, restart when code on disk
    does not match the running Brain (or when ``force`` is True for one-click update).

    Returns a JSON-able dict. Never raises — failures come back as {success: False}.
    """
    from src.shared.runtime_identity import (
        brain_runtime_stale,
        get_version_snapshot,
        gui_runtime_stale,
    )
    from src.shared.ui_build import install_web_client_deps, needs_web_rebuild, rebuild_web_client

    repo_root = Path(__file__).parents[2]
    dev_stack = _is_dev_brain()
    steps: list[str] = []
    from_sha: str | None = None

    try:
        _, status_out, _ = await asyncio.to_thread(
            _run_git, ["git", "status", "--porcelain"], repo_root
        )
        if _meaningful_dirty_paths(status_out):
            return {
                "success": False,
                "error": "Commit or stash local changes before switching branches.",
            }

        rc, out, err = await asyncio.to_thread(_run_git, ["git", "fetch", "origin", branch], repo_root)
        if rc != 0:
            return {"success": False, "error": f"git fetch failed: {err or out}"}

        _, local_head, _ = await asyncio.to_thread(_run_git, ["git", "rev-parse", "HEAD"], repo_root)
        rc, remote_head, err = await asyncio.to_thread(_run_git, ["git", "rev-parse", f"origin/{branch}"], repo_root)
        if rc != 0:
            return {"success": False, "error": f"cannot resolve origin/{branch}: {err}"}

        git_pulled = False
        changed: list[str] = []

        if local_head != remote_head:
            _, diff_files, _ = await asyncio.to_thread(
                _run_git, ["git", "diff", "--name-only", f"{local_head}..{remote_head}"], repo_root
            )
            changed = [f.strip() for f in diff_files.splitlines() if f.strip()]
            deps_changed = any(f == "requirements.txt" for f in changed)
            from_sha = local_head

            checkout_branch = "chad-dev" if os.environ.get("CHAD_DEV_WORKTREE") else branch
            rc, out, err = await asyncio.to_thread(
                _run_git,
                ["git", "checkout", "-B", checkout_branch, f"origin/{branch}"],
                repo_root,
            )
            if rc != 0:
                return {"success": False, "error": f"git checkout failed: {err or out}"}
            if checkout_branch == "chad-dev":
                await asyncio.to_thread(
                    _run_git,
                    ["git", "branch", "-u", f"origin/{branch}", "chad-dev"],
                    repo_root,
                )

            steps.append(f"Pulled {local_head[:8]} → {remote_head[:8]} ({len(changed)} files)")
            git_pulled = True
            local_head = remote_head

            if deps_changed:
                main_root = os.environ.get("CHAD_MAIN_ROOT")
                py = str(
                    (Path(main_root) if main_root else repo_root)
                    / ".venv" / "Scripts" / "python.exe"
                )
                rc, out, err = await asyncio.to_thread(
                    _run_git, [py, "-m", "pip", "install", "-r", "requirements.txt"], repo_root, 600
                )
                step = "Reinstalled Python deps" if rc == 0 else f"pip install FAILED: {(err or out)[-300:]}"
                steps.append(step)
                if rc != 0:
                    return {"success": False, "updated": True, "error": step, "steps": steps}

        brain_stale, brain_reason = brain_runtime_stale(repo_root)
        gui_stale, gui_reason = gui_runtime_stale(repo_root)

        web_rebuilt = False
        if dev_stack:
            # Chad Dev loads UI from Vite (npm run dev), not dist/ — never run prod build.
            web_deps_changed = any(
                f in {"src/clients/web/package.json", "src/clients/web/package-lock.json"}
                for f in changed
            )
            if git_pulled and web_deps_changed:
                ok, detail = await install_web_client_deps(repo_root)
                if not ok:
                    return {
                        "success": False,
                        "updated": git_pulled,
                        "error": detail,
                        "steps": steps + [detail],
                    }
                steps.append(detail)
        else:
            web_needs_rebuild = needs_web_rebuild(changed if git_pulled else [], local_head, repo_root)
            if web_needs_rebuild:
                ok, detail = await rebuild_web_client(repo_root, install_deps=True)
                if not ok:
                    return {
                        "success": False,
                        "updated": git_pulled,
                        "error": detail,
                        "steps": steps + [detail],
                    }
                steps.append(detail)
                web_rebuilt = True

        must_restart_brain = git_pulled or brain_stale or web_rebuilt or force
        must_restart_gui = must_restart_brain or (gui_stale and not dev_stack) or force

        if not must_restart_brain and not must_restart_gui:
            snap = get_version_snapshot(repo_root)
            return {
                "success": True,
                "updated": False,
                "restarting": False,
                "gui_restart_recommended": False,
                "message": "Already up to date — Brain and GUI are running the latest code.",
                "version": snap,
                "head": local_head[:8],
            }

        restart_reasons: list[str] = []
        if git_pulled:
            restart_reasons.append("git_pull")
        if brain_stale:
            restart_reasons.append(f"brain_stale:{brain_reason}")
            steps.append(
                "Brain was running stale code (disk ahead of loaded process) — restart scheduled"
            )
        if web_rebuilt:
            restart_reasons.append("web_rebuild")
        if gui_stale:
            restart_reasons.append(f"gui_stale:{gui_reason}")
        if force and not restart_reasons:
            restart_reasons.append("user_requested")
            steps.append("User update — restarting Brain and GUI to apply current code")

        if must_restart_brain:
            _schedule_brain_restart(server)
            if "Brain restart scheduled" not in steps:
                steps.append("Brain restart scheduled")

        if git_pulled:
            message = "Update applied. Chad will close and reopen automatically."
        elif brain_stale:
            message = (
                "Code on disk is newer than the running Brain — restarting to load it. "
                "Chad will close and reopen automatically."
            )
        elif force:
            message = "Restarting Brain and GUI to ensure the latest code is active."
        elif web_rebuilt or gui_stale:
            message = "UI rebuild applied. Chad will close and reopen automatically."
        else:
            message = "Restart scheduled."

        logger.warning(f"[ UPDATE ] : {' | '.join(steps)}")
        snap = get_version_snapshot(repo_root)
        return {
            "success": True,
            "updated": bool(git_pulled or web_rebuilt or brain_stale or gui_stale or force),
            "from": from_sha[:8] if from_sha else None,
            "to": local_head[:8],
            "steps": steps,
            "restarting": must_restart_brain,
            "gui_restart_recommended": must_restart_gui,
            "restart_reasons": restart_reasons,
            "version": snap,
            "message": message,
            "head": local_head[:8],
        }

    except Exception as e:
        logger.error(f"Update failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def _check_update_token(request) -> bool:
    """Shared token guard for the update/settings endpoints (x-chad-token or ?token=).
    Loopback callers (the local desktop GUI) are always allowed; remote callers
    need a matching token. ChadServer generates+persists one on first boot if
    none is configured (see server.py), so this fails closed if it's somehow
    still missing rather than allowing unauthenticated remote access."""
    from src.shared.config_manager import ConfigManager
    if _is_loopback_request(request):
        return True
    expected = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
    if not expected:
        return False
    provided = request.headers.get("x-chad-token") or request.query_params.get("token")
    return provided == expected


async def _system_settings_endpoint(request):
    """GET → current version + auto-update settings. POST → update them (persisted to
    the preferences layer, applied live). Authenticated, so it can drive the in-GUI
    Settings → System screen for both desktop and browser clients."""
    from src.shared.config_manager import ConfigManager
    if not _check_update_token(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        patch = {}
        if "auto_update" in body:
            patch["auto_update"] = bool(body["auto_update"])
        if "auto_update_interval_minutes" in body:
            try:
                patch["auto_update_interval_minutes"] = max(5, int(body["auto_update_interval_minutes"]))
            except (TypeError, ValueError):
                pass
        if patch:
            try:
                # Apply to the running config and persist to the preferences layer so it
                # survives restarts (mirrors the SET_MODEL_CONFIG path in limb_coordinator).
                from src.infrastructure.repositories.config_repository import ConfigRepository
                ConfigManager._recursive_update(ConfigManager._config, {"updates": patch})
                repo = ConfigRepository()
                prefs = repo.load("preferences", use_cache=False)
                ConfigManager._recursive_update(prefs, {"updates": patch})
                repo.save("preferences", prefs)
                logger.warning(f"[ SETTINGS ] : updates.* set to {patch}")
            except Exception as e:
                logger.error(f"[ SETTINGS ] : Failed to persist update settings: {e}")
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    return JSONResponse({
        "success": True,
        "version": get_version_info(),
        "auto_update": bool(ConfigManager.get("updates.auto_update", False)),
        "auto_update_interval_minutes": ConfigManager.get("updates.auto_update_interval_minutes", 60),
        "dev_stack": _is_dev_brain(),
        "dev_worktree": bool(os.environ.get("CHAD_DEV_WORKTREE")),
    })


async def _update_endpoint(request):
    """Pull origin/<branch>, reinstall changed deps, graceful restart.

    Auth: requires x-chad-token (or ?token=) matching CHAD_SERVER_TOKEN — same guard as
    the command relay. The external supervisor relaunches the process after the graceful
    shutdown, so the restarted Brain is running freshly pulled code.

    POST body (optional):
      ``{"force": true}`` — always restart Brain+GUI even when git is current.
      ``{"branch": "feature/foo"}`` — switch to a pushed remote branch (dev stack only).
    """
    if not _check_update_token(request):
        client_host = request.client.host if request.client else "Unknown"
        logger.warning(f"[ UPDATE ] : Unauthorized update attempt from IP={client_host}")
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    force = False
    branch = "master"
    try:
        body = await request.json()
        if isinstance(body, dict):
            force = bool(body.get("force", False))
            branch = str(body.get("branch", "master")).strip() or "master"
    except Exception:
        pass

    blocked = _branch_switch_blocked(branch)
    if blocked:
        status = 403 if "8788" in blocked else 400
        return JSONResponse({"success": False, "error": blocked}, status_code=status)

    server = getattr(request.app.state, "server", None)
    result = await perform_self_update(server=server, branch=branch, force=force)
    status = 200 if result.get("success") else 500
    return JSONResponse(result, status_code=status)


async def _branches_endpoint(request):
    """List pushed remote branches for the dev branch switcher (Settings → System)."""
    if not _check_update_token(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    repo_root = Path(__file__).parents[2]
    try:
        rc, _, err = await asyncio.to_thread(
            _run_git, ["git", "fetch", "--prune", "origin"], repo_root
        )
        if rc != 0:
            return JSONResponse(
                {"success": False, "error": f"git fetch failed: {err}"},
                status_code=500,
            )

        rc, out, err = await asyncio.to_thread(
            _run_git,
            ["git", "branch", "-r", "--format=%(refname:short)"],
            repo_root,
        )
        if rc != 0:
            return JSONResponse(
                {"success": False, "error": f"git branch failed: {err or out}"},
                status_code=500,
            )

        version = get_version_info()
        return JSONResponse({
            "success": True,
            "current": version.get("branch"),
            "branches": _parse_remote_branches(out),
        })
    except Exception as e:
        logger.error(f"[ BRANCHES ] : Failed to list branches: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def _feedback_endpoint(request):
    """Handle thumbs up/down user feedback for a specific turn."""
    try:
        session_id = request.path_params.get("session_id")
        turn_id = request.path_params.get("turn_id")
        
        if not session_id or not turn_id:
            return JSONResponse({"success": False, "error": "Missing session_id or turn_id"}, status_code=400)
            
        payload = await request.json()
        rating = payload.get("rating")
        if rating not in ["positive", "negative"]:
            return JSONResponse({"success": False, "error": "Rating must be 'positive' or 'negative'"}, status_code=400)
            
        logger.info(f"[ FEEDBACK ] Turn {turn_id} rated as '{rating}'")
        
        # 1. Update user_rating in tool_outcome_log for all tool runs linked to this turn_id
        from src.infrastructure.database.supabase_manager import get_supabase_manager
        supabase = get_supabase_manager()
        
        if supabase:
            # Credit assignment: a turn-level rating must NOT blanket-stamp every tool
            # in a multi-tool turn. OATS uses user_rating as a weight-1.0 override, so a
            # thumbs-down on a turn where tool A succeeded and tool B failed would wrongly
            # banish the correct tool A. Align the human signal with each tool's actual
            # execution outcome: thumbs-down repels only tools that FAILED; thumbs-up
            # rewards only tools that SUCCEEDED. (Tools whose individual outcome contradicts
            # the turn rating are left untouched — we can't attribute blame from turn-level
            # signal alone, so we stay conservative rather than punish a correct tool.)
            outcome_for_rating = "failure" if rating == "negative" else "success"
            if not supabase.enabled:
                # Route the human feedback signal to the local shim so thumbs up/down still
                # weights OATS refinement offline (issue #470); register the table first.
                from src.core.orchestration.oats_refiner import ensure_local_oats_tables
                ensure_local_oats_tables()
            await supabase.async_update(
                "tool_outcome_log",
                filters={"turn_id": f"eq.{turn_id}", "outcome": f"eq.{outcome_for_rating}"},
                data={"user_rating": rating}
            )

            # 2. Persist the rating on the message metadata so the badge survives a reload.
            # Goes through ChatHistoryRepository so it works on both backends: Supabase when
            # configured, local sqlite (via local_store) otherwise — no data lost offline (#470).
            from src.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
            repo = ChatHistoryRepository()
            await repo.update_message_metadata(turn_id, {"user_rating": rating})
        
        # 3. Publish to EventBus
        server = getattr(request.app.state, "server", None)
        if server and server._event_bus:
            server._event_bus.publish(TurnFeedbackEvent(
                session_id=session_id,
                turn_id=turn_id,
                rating=rating
            ))
            
        return JSONResponse({"success": True})
        
    except Exception as e:
        logger.error(f"Feedback processing failed: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)



async def _test_mcp_connection_endpoint(request):
    """Validate an MCP server connection without persisting it to config."""
    try:
        body = await request.json()
        name = body.get("name", "test-server")
        command = body.get("command", [])
        env = body.get("env", {})
        extra = body.get("extra") or {}

        if not command:
            return JSONResponse({"success": False, "error": "command is required"}, status_code=400)

        from src.core.tools.mcp_registry import MCPServerEntry
        config = {k: v for k, v in extra.items() if v is not None}
        entry = MCPServerEntry(name=name, command=command, env=env, config=config)
        try:
            await entry.connect()
            tools = await entry.list_tools()
            tool_names = [t.get("name", "") for t in tools]
            return JSONResponse({"success": True, "tools": tool_names})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)})
        finally:
            await entry.disconnect()
    except Exception as e:
        logger.error(f"MCP connection test failed: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def _push_vapid_key_endpoint(request):
    """Return the VAPID public key so the browser can create a push subscription."""
    from src.remote.push_service import get_push_service
    svc = get_push_service()
    if not svc.public_key:
        return JSONResponse({"error": "Push not configured"}, status_code=503)
    return JSONResponse({"public_key": svc.public_key})


async def _push_subscribe_endpoint(request):
    """Store a browser push subscription."""
    from src.remote.push_service import get_push_service
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    if not body.get("endpoint"):
        return JSONResponse({"error": "Missing endpoint"}, status_code=400)
    get_push_service().add_subscription(body)
    return JSONResponse({"ok": True})


async def _push_unsubscribe_endpoint(request):
    """Remove a push subscription by endpoint URL."""
    from src.remote.push_service import get_push_service
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    endpoint = body.get("endpoint", "")
    if endpoint:
        get_push_service().remove_subscription(endpoint)
    return JSONResponse({"ok": True})


def build_starlette_routes(ws_route_handler, daemon_ws_handler=None) -> list:
    """
    Generates the standard route registry for Starlette.
    
    Args:
        ws_route_handler: The websocket gateway callable for visual clients/limbs.
        daemon_ws_handler: Optional endpoint for OS Hands daemons.
    """
    routes = [
        # Health check for Render (PRIORITIZE HTTP)
        Route("/health", _health_endpoint, methods=["GET", "HEAD"]),

        # Desktop buddy — snapshot + SSE push stream
        Route("/buddy/state", _buddy_state_endpoint, methods=["GET"]),
        Route("/buddy/stream", _buddy_stream_endpoint, methods=["GET"]),

        # WebSockets (Eyes / Clients)
        WebSocketRoute("/ws", ws_route_handler),
        WebSocketRoute("/", ws_route_handler), # Fallback for old limbs
    ]

    # Register Hands Daemon endpoint if provided (Hands)
    if daemon_ws_handler:
        routes.append(WebSocketRoute("/ws/daemon", daemon_ws_handler))

    routes.extend([

        # Remote Command Gateway (Relays headless operations)
        Route("/api/command", _execute_command_endpoint, methods=["POST"]),

        # MCP connector — live connection test before saving
        Route("/api/connectors/test", _test_mcp_connection_endpoint, methods=["POST"]),

        # Webhook endpoints
        Route("/webhooks/gmail", _webhook_gmail_endpoint, methods=["POST"]),
        Route("/webhooks/telegram", _webhook_telegram_endpoint, methods=["POST"]),
        Route("/webhooks/github", _webhook_github_endpoint, methods=["POST"]),
        
        # Direct Downloads
        Route("/files/{token}", _download_file_endpoint, methods=["GET"]),
        
        # Artifacts Management
        Route("/artifacts", _list_artifacts_endpoint, methods=["GET"]),
        
        # Manual triggers
        Route("/heartbeat/trigger", _trigger_heartbeat_endpoint, methods=["POST", "GET"]),

        # Deployed version probe (public — SHA/date/branch only)
        Route("/api/version", _version_endpoint, methods=["GET"]),

        # Services health — aggregate ping of Brain/LM Studio/SearXNG/Kroki/Typst/Marp (issue #363)
        Route("/api/services/health", _services_health_endpoint, methods=["GET"]),

        # System settings: version + auto-update toggle (GET read, POST update)
        Route("/api/system/settings", _system_settings_endpoint, methods=["GET", "POST"]),

        # One-click update / dev branch switch (pull origin/<branch> + graceful restart)
        Route("/api/update", _update_endpoint, methods=["POST"]),
        Route("/api/branches", _branches_endpoint, methods=["GET"]),
        
        # User Feedback for OATS refinement
        Route("/api/sessions/{session_id}/turns/{turn_id}/feedback", _feedback_endpoint, methods=["POST"]),

        # Web Push — VAPID public key + subscription management
        Route("/api/push/vapid-key", _push_vapid_key_endpoint, methods=["GET"]),
        Route("/api/push/subscribe", _push_subscribe_endpoint, methods=["POST"]),
        Route("/api/push/unsubscribe", _push_unsubscribe_endpoint, methods=["POST"]),
    ])

    # Serve the built React web client (PWA) at the root, so a phone/browser on the
    # tailnet can load the app AND talk to the API/WS from a SINGLE origin
    # (http://<tailnet-ip>:8767) — no separate Vite dev server, no cross-origin CORS.
    # Mounted LAST so every API/WS/webhook route above is matched first; the SPA
    # catch-all (html=True) only handles otherwise-unmatched GETs. The WebSocketRoute
    # on "/" still wins for ws upgrades (different ASGI scope). Skipped if the client
    # hasn't been built yet (dev without `npm run build`).
    dist_dir = Path(__file__).resolve().parents[2] / "src" / "clients" / "web" / "dist"
    if (dist_dir / "index.html").exists():
        routes.append(Mount("/", app=StaticFiles(directory=str(dist_dir), html=True), name="webapp"))
        logger.info(f"[ ROUTES ] : Serving web client from {dist_dir}")
    else:
        logger.warning(f"[ ROUTES ] : Web client bundle not found at {dist_dir} (run `npm run build`); phone access needs the Vite dev server.")

    return routes
