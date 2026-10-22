import os
import sys
import asyncio
import shutil
import webview
import webbrowser
import subprocess
from urllib.parse import unquote
from src.shared.logger import setup_logger
from src.clients.desktop.api.base import _dialog_queue
from src.application.commands import (
    StopActiveTaskCommand,
    GetCapabilitySuggestionsCommand,
    SetCapabilitySuggestionStatusCommand,
    ProposeCapabilitySkillBuildCommand,
    CommitCapabilitySkillBuildCommand,
    GetReflectionLessonsCommand,
    SetReflectionLessonStatusCommand,
    AnswerReflectionQuestionCommand,
    GetDashboardFeedCommand,
    RunDashboardActionCommand,
    DismissDashboardEntryCommand
)

logger = setup_logger("WebViewAPI")


class SystemAPIMixin:
    """Mixin for system, file dialogs, and platform integration WebView API methods."""

    def get_capability_suggestions(self, limit=5):
        """Fetch pending capability suggestions for the Suggestions side panel."""
        return self._execute_command(
            GetCapabilitySuggestionsCommand(limit=int(limit)),
            timeout=15.0,
            default_fallback=[],
            raise_on_error=False
        )

    def set_capability_suggestion_status(self, suggestion_id, status):
        """Accept or dismiss a capability suggestion (status transition only)."""
        return self._execute_command(
            SetCapabilitySuggestionStatusCommand(suggestion_id=suggestion_id, status=status),
            timeout=15.0,
            default_fallback={"success": False},
            raise_on_error=False
        )

    def propose_capability_skill_build(self, suggestion_id):
        """Draft agent.py + SKILL.md for an accepted skill suggestion and mint an
        approval token for the human to review. Writes nothing to disk yet."""
        return self._execute_command(
            ProposeCapabilitySkillBuildCommand(suggestion_id=suggestion_id),
            timeout=15.0,
            default_fallback={"success": False},
            raise_on_error=False
        )

    def commit_capability_skill_build(self, suggestion_id, approval_token):
        """Consume the approval token and write the scaffolded skill to disk."""
        return self._execute_command(
            CommitCapabilitySkillBuildCommand(suggestion_id=suggestion_id, approval_token=approval_token),
            timeout=15.0,
            default_fallback={"success": False},
            raise_on_error=False
        )

    def get_reflection_lessons(self, limit=20):
        """Fetch the pending (+ auto-applied) reflection lessons/questions queue."""
        return self._execute_command(
            GetReflectionLessonsCommand(limit=int(limit)),
            timeout=15.0,
            default_fallback=[],
            raise_on_error=False
        )

    def set_reflection_lesson_status(self, lesson_id, status):
        """Approve (write it) or dismiss a reflection lesson."""
        return self._execute_command(
            SetReflectionLessonStatusCommand(lesson_id=lesson_id, status=status),
            timeout=15.0,
            default_fallback={"success": False},
            raise_on_error=False
        )

    def answer_reflection_question(self, lesson_id, answer):
        """Submit the user's answer to an open reflection question."""
        return self._execute_command(
            AnswerReflectionQuestionCommand(lesson_id=lesson_id, answer=answer),
            timeout=15.0,
            default_fallback={"success": False},
            raise_on_error=False
        )

    def get_action_history(self):
        """JavaScript can call this to get action history."""
        window = self.main_window_ref()
        if window:
            return window.action_history_data
        return []
    
    def get_live_logs(self):
        """JavaScript can call this to get live logs."""
        window = self.main_window_ref()
        if window:
            return window.live_logs_data
        return []
    
    def get_caption(self):
        """JavaScript can call this to get current caption text."""
        window = self.main_window_ref()
        if window:
            return window.current_caption_text
        return ""
    
    def get_status(self):
        """JavaScript can call this to get current status text."""
        window = self.main_window_ref()
        if window:
            return window.current_status_text
        return ""
    
    def get_dashboard_feed(self, limit=50):
        """Fetch the dashboard feed (pinned life widgets + Chad's reverse-chron entries)."""
        return self._execute_command(
            GetDashboardFeedCommand(limit=int(limit)),
            timeout=15.0,
            default_fallback=[],
            raise_on_error=False
        )

    def run_dashboard_action(self, entry_id):
        """Run the single step attached to a dashboard 'action' card."""
        return self._execute_command(
            RunDashboardActionCommand(entry_id=entry_id),
            timeout=60.0,
            default_fallback={"success": False, "error": "Command timed out"},
            raise_on_error=False
        )

    def dismiss_dashboard_entry(self, entry_id):
        """Dismiss a dashboard entry."""
        return self._execute_command(
            DismissDashboardEntryCommand(entry_id=entry_id),
            timeout=15.0,
            default_fallback={"success": False},
            raise_on_error=False
        )

    def get_server_url(self):
        """Expose the Brain HTTP origin to JS (no /ws suffix — for REST API calls)."""
        from src.remote.limb import resolve_http_base

        return resolve_http_base()

    def get_pairing_info(self) -> dict:
        """Pairing URLs for a phone/PWA, each with the server token embedded as a query param.

        Deliberately a **pywebview API method, not an HTTP endpoint**. This returns the
        credential that unlocks the Brain, so it must not be fetchable over the network:
        the pywebview bridge is in-process to the desktop Limb, so the token is read from
        local config and handed straight to the local WebView. It never crosses a socket,
        and there is no route a phone (or anything else on the tailnet) could call to ask
        for it.

        The client strips ``serverUrl``/``token`` from the address bar on arrival
        (``transportConfig.ingestQueryParams``), so the token doesn't linger in history —
        but it IS in the URL while in flight, which is why the QR is only ever rendered on
        the desktop GUI and never transmitted anywhere.

        Candidates are ordered best-first:
          1. Tailscale MagicDNS over HTTPS — the only one that can install as a PWA, since
             a service worker needs a secure context. Requires ``tailscale serve``.
          2. The raw tailnet IP — works in a phone browser, but http:// means no install.
        """
        from urllib.parse import urlencode, urlparse
        from src.shared.config_manager import ConfigManager

        token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
        if not token:
            return {"success": False, "error": "No CHAD_SERVER_TOKEN configured.", "candidates": []}

        try:
            brain_port = urlparse(self.get_server_url()).port or 8767
        except Exception:
            brain_port = 8767

        def _ts(args: list) -> str:
            for exe in ("tailscale", r"C:\Program Files\Tailscale\tailscale.exe"):
                try:
                    out = subprocess.run([exe] + args, capture_output=True, text=True, timeout=5)
                except Exception:
                    continue
                if out.returncode == 0:
                    return out.stdout or ""
            return ""

        candidates = []

        # 1. MagicDNS over HTTPS — only offered when `tailscale serve` is actually proxying,
        # since the hostname resolves either way and a URL that 404s is worse than no URL.
        dns_name = ""
        try:
            import json as _json
            status = _json.loads(_ts(["status", "--json"]) or "{}")
            dns_name = str((status.get("Self") or {}).get("DNSName") or "").rstrip(".")
        except Exception:
            dns_name = ""
        serve_status = _ts(["serve", "status"])
        if dns_name and dns_name in serve_status:
            candidates.append({
                "label": "Tailscale HTTPS — installable as an app",
                "base": f"https://{dns_name}",
                "installable": True,
            })

        # 2. Raw tailnet IP — reachable, but http:// is not a secure context, so no
        # service worker and no "Add to Home Screen" as a real app.
        for ip in (_ts(["ip", "-4"]) or "").split():
            ip = ip.strip()
            if ip:
                candidates.append({
                    "label": f"Tailnet IP {ip} — browser only, cannot install",
                    "base": f"http://{ip}:{brain_port}",
                    "installable": False,
                })

        if not candidates:
            return {
                "success": False,
                "error": "Tailscale isn't reachable, so there's no address a phone could use.",
                "candidates": [],
            }

        for c in candidates:
            c["pairing_url"] = f"{c['base']}/?" + urlencode({"serverUrl": c["base"], "token": token})

        return {"success": True, "candidates": candidates}

    def update_chad(self, branch: str = None) -> dict:
        """Pull origin/<branch> onto the running Brain, restart it, and relaunch the GUI.

        Calls the Brain's authenticated /api/update endpoint. On success the Brain
        restarts (supervisor relaunches it). When anything actually changed, we also
        restart the desktop window so WebView loads the fresh dist/ bundle.

        Args:
            branch: Remote branch to switch to (default ``master``). Dev stack only
                for non-master branches.
        """
        import httpx
        from src.shared.config_manager import ConfigManager

        http_url = self.get_server_url()
        token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
        headers = {"x-chad-token": token} if token else {}
        payload: dict = {"force": True}
        if branch:
            payload["branch"] = str(branch).strip()

        try:
            logger.warning(
                "[ UPDATE ] : User requested Chad update — calling Brain /api/update "
                f"(branch={payload.get('branch', 'master')}, force)"
            )
            resp = httpx.post(
                f"{http_url}/api/update",
                headers=headers,
                json=payload,
                timeout=600.0,
            )
            data = resp.json()
            if data.get("success") and (
                data.get("restarting") or data.get("gui_restart_recommended")
            ):
                from src.shared.gui_restart import spawn_gui_relaunch_watchdog

                spawn_gui_relaunch_watchdog()
                data["gui_restarting"] = True
                data.setdefault(
                    "message",
                    "Update applied. Chad will close and reopen automatically.",
                )
            elif data.get("restarting"):
                try:
                    self.reconnect_server()
                except Exception:
                    pass
            return data
        except httpx.HTTPError as e:
            logger.error(f"[ UPDATE ] : Update request failed: {e}")
            return {"success": False, "error": str(e)}

    def get_branches(self) -> dict:
        """List pushed remote branches for the dev branch switcher."""
        import httpx
        from src.shared.config_manager import ConfigManager

        http_url = self.get_server_url()
        token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
        headers = {"x-chad-token": token} if token else {}
        try:
            resp = httpx.get(f"{http_url}/api/branches", headers=headers, timeout=30.0)
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"[ BRANCHES ] : get_branches failed: {e}")
            return {"success": False, "error": str(e)}

    def trigger_restart(self) -> None:
        """Called by JS after it has rendered the update-success message.
        Closes the webview window so the watchdog (already running) can relaunch it."""
        from src.shared.gui_restart import close_window_for_restart

        close_window_for_restart(self.main_window_ref, delay_s=0.15)

    def get_system_settings(self) -> dict:
        """Fetch the Brain's deployed version + auto-update settings (for Settings → System)."""
        import httpx
        from src.shared.config_manager import ConfigManager
        http_url = self.get_server_url()
        token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
        headers = {"x-chad-token": token} if token else {}
        try:
            resp = httpx.get(f"{http_url}/api/system/settings", headers=headers, timeout=15.0)
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"[ SETTINGS ] : get_system_settings failed: {e}")
            return {"success": False, "error": str(e)}

    def get_services_health(self) -> dict:
        """Fetch health of Chad's infrastructure dependencies (for Settings → Services, issue #371)."""
        import httpx
        from src.shared.config_manager import ConfigManager
        http_url = self.get_server_url()
        token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
        headers = {"x-chad-token": token} if token else {}
        try:
            resp = httpx.get(f"{http_url}/api/services/health", headers=headers, timeout=15.0)
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"[ SERVICES ] : get_services_health failed: {e}")
            return {"success": False, "error": str(e)}

    def set_auto_update(self, enabled: bool, interval: int = None) -> dict:
        """Enable/disable auto-update (and optionally set the check interval, minutes)."""
        import httpx
        from src.shared.config_manager import ConfigManager
        http_url = self.get_server_url()
        token = ConfigManager.get_secret("CHAD_SERVER_TOKEN") or ConfigManager.get_secret("CHAD_REMOTE_TOKEN")
        headers = {"x-chad-token": token} if token else {}
        body = {"auto_update": bool(enabled)}
        if interval is not None:
            body["auto_update_interval_minutes"] = int(interval)
        try:
            resp = httpx.post(f"{http_url}/api/system/settings", headers=headers, json=body, timeout=15.0)
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"[ SETTINGS ] : set_auto_update failed: {e}")
            return {"success": False, "error": str(e)}

    def download_artifact(self, token: str) -> dict:
        """Resolve a file token and trigger native OS save dialog."""
        import base64
        import tempfile
        from src.shared.file_registry import file_registry
        
        try:
            metadata = file_registry.get_file(token)
            if not metadata:
                logger.error(f"[ API ] : Download failed - Token {token} not found")
                return {'success': False, 'error': 'File not found'}
            
            name = metadata.get('name', 'download')
            data = base64.b64decode(metadata.get('data', ''))
            suffix = os.path.splitext(name)[1] or ''
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            
            # Trigger native OS save dialog with the original filename
            self.download_local_file(tmp_path, forced_filename=name)
            
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except:
                pass
            
            return {'success': True}
        except Exception as e:
            logger.error(f"[ API ] : download_artifact failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def get_thinking(self):
        """JavaScript can call this to get current thinking text."""
        window = self.main_window_ref()
        if window:
            return window.current_thinking_text
        return ""
    
    def open_local_file(self, path):
        """JavaScript can call this to open a local file directly."""
        try:
            if path.startswith('file:///'):
                path = path[8:]
            elif path.startswith('file://'):
                path = path[7:]
            path = unquote(path)
            path = os.path.abspath(path)
            if not os.path.exists(path):
                return False
            if sys.platform == 'win32':
                os.startfile(path)
            else:
                subprocess.Popen(['xdg-open', path])
            return True
        except Exception:
            return False

    def open_external_url(self, url):
        """JavaScript can call this to open a web URL in the system's default browser."""
        try:
            # Basic validation
            if not url or not isinstance(url, str):
                return False
            
            # Ensure it's a web URL
            if not (url.startswith('http://') or url.startswith('https://')):
                return False
                
            webbrowser.open(url)
            return True
        except Exception as e:
            logger.error(f"Error opening external URL {url}: {e}")
            return False

    def download_local_file(self, path, forced_filename=None):
        """JavaScript can call this to prompt the user to save/download a file."""
        try:
            if path.startswith('file:///'):
                path = path[8:]
            elif path.startswith('file://'):
                path = path[7:]
            path = unquote(path)
            path = os.path.abspath(path)
            
            if not os.path.exists(path):
                return False

            basename = forced_filename or os.path.basename(path)
            
            # Determine Users default Documents folder
            if sys.platform == 'win32':
                import winreg
                try:
                    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
                    docs_dir = winreg.QueryValueEx(k, "Personal")[0]
                except Exception:
                    docs_dir = os.path.expanduser("~\\Documents")
            else:
                docs_dir = os.path.expanduser("~/Documents")
                
            default_save_dir = os.path.join(docs_dir, "Chad Documents")
            os.makedirs(default_save_dir, exist_ok=True)

            window = self.main_window_ref()
            wb_window = window.webview_window if window else webview.active_window()
            
            if wb_window:
                result = wb_window.create_file_dialog(
                    webview.SAVE_DIALOG, 
                    directory=default_save_dir, 
                    save_filename=basename
                )
                if result and len(result) > 0:
                    dest_path = result[0]
                    shutil.copy2(path, dest_path)
                    
                    # Open the file automatically after downloading
                    if sys.platform == 'win32':
                        os.startfile(dest_path)
                    else:
                        subprocess.Popen(['xdg-open', dest_path])
                    return True
            return False
        except Exception as e:
            logger.error(f"Error downloading local file {path}: {e}")
            return False

    def poll_dialog_queue(self):
        """Drains the task queue. Called by JavaScript interval to run tasks on the main thread."""
        try:
            while not _dialog_queue.empty():
                task = _dialog_queue.get_nowait()
                if callable(task):
                    task()
        except queue.Empty:
            pass
        except Exception as e:
            logger.error(f"Error in poll_dialog_queue: {e}")

    def toggle_voice_mode(self, enabled: bool):
        """Toggle voice mode (wake word detection and audio input) on/off."""
        window = self.main_window_ref()
        if not window:
            return False
        
        app = window.app_reference
        if not app:
            return False
        
        try:
            app.set_voice_mode(enabled)
            mode_str = "enabled" if enabled else "disabled"
            logger.info(f"Voice mode {mode_str}")
            return True
        except Exception as e:
            logger.error(f"Failed to toggle voice mode: {e}", exc_info=True)
            return False

    def get_voice_mode_state(self):
        """Get the current voice mode state."""
        window = self.main_window_ref()
        if window and window.app_reference:
            return getattr(window.app_reference, 'voice_mode_enabled', True)
        return True

    def stop_active_task(self):
        """JavaScript can call this to immediately stop any active AI processing."""
        window = self.main_window_ref()
        if window and window.app_reference:
            loop = getattr(window.app_reference, 'loop', None)

            # 1. Flush buffered stream events, emit run_cancelled, commit partial bubble,
            #    and clear limb state — all via the normal turn-end code path.
            #    This MUST happen before any frontend state wipe so push_ai_response
            #    fires while streamTurn still holds the accumulated thoughts.
            limb = getattr(window.app_reference, 'uplink_limb', None)
            if limb and hasattr(limb, 'abort_active_turn'):
                limb.abort_active_turn(loop=loop)

            # 2. Signal the backend Brain to stop processing (fire-and-forget).
            handler = self._get_command_handler()
            if handler and loop:
                asyncio.run_coroutine_threadsafe(
                    handler.execute(StopActiveTaskCommand()), loop
                )
            return True
        return False

    def pick_file(self):
        """
        Open a native file dialog to pick a file.
        Returns a dict with {success, path, name, content} or {success, error}.
        """
        window = self.main_window_ref()
        if not window:
            return {"success": False, "error": "Main window reference lost"}

        # Get the webview window instance
        webview_window = getattr(window, 'webview_window', None)
        if not webview_window:
            return {"success": False, "error": "Webview window not available"}

        try:
            # Open file dialog
            # Create a file dialog that allows selecting a single file
            file_paths = webview_window.create_file_dialog(
                dialog_type=webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=('All files (*.*)',)
            )

            if not file_paths:
                return {"success": False, "error": "Selection cancelled"}

            file_path = file_paths[0]
            file_name = os.path.basename(file_path)
            
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(100000) # Limit to 100k chars for now
                    if len(content) >= 100000:
                        content += "\n... (File truncated)"
            except Exception as e:
                logger.warning(f"Failed to read file content: {e}")
                content = f"[ Attachment: {file_path} (could not read content) ]"

            logger.info(f"User picked file: {file_path}")
            
            return {
                "success": True,
                "path": file_path,
                "name": file_name,
                "content": content
            }
            
        except Exception as e:
            logger.error(f"Error picking file: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def confirm_tool_limit_extension(self, approved: bool):
        """Called by JS to resolve the tool limit confirmation."""
        window = self.main_window_ref()
        if not window: return False

        app = window.app_reference

        # Phase 5: EventBus-based confirmation (SessionManager)
        if app and hasattr(app, 'assistant') and app.assistant and hasattr(app, '_pending_tool_confirmation_request_id'):
            request_id = app._pending_tool_confirmation_request_id
            from src.application.events import ToolLimitConfirmationResponse
            app.assistant.event_bus.publish(ToolLimitConfirmationResponse(
                request_id=request_id,
                approved=approved
            ))
            # Clean up
            delattr(app, '_pending_tool_confirmation_request_id')
            logger.info(f"[ EVENTBUS ] Tool limit confirmation response sent: {approved} (request_id={request_id})")
            return True

        # Legacy: Direct future resolution (TextModeClient)
        if app and hasattr(app, '_tool_confirmation_future') and app._tool_confirmation_future:
            if not app._tool_confirmation_future.done():
                # Resolve the future that TextModeClient is waiting on
                # Use call_soon_threadsafe to interact with the loop from this thread
                app.loop.call_soon_threadsafe(
                    app._tool_confirmation_future.set_result,
                    approved
                )
                return True
        return False

    def confirm_file_plan_approval(self, token: str, approved: bool):
        """Called by JS when user clicks Approve/Reject on a file plan."""
        window = self.main_window_ref()
        if not window:
            return False
        
        app = window.app_reference
        if not app:
            return False
        
        # Check for pending file plan approval future
        if hasattr(app, '_file_plan_futures') and token in app._file_plan_futures:
            future = app._file_plan_futures[token]
            if not future.done():
                logger.info(f"[ FILE_PLAN ] User {'approved' if approved else 'rejected'} plan with token {token}")
                app.loop.call_soon_threadsafe(
                    future.set_result,
                    approved
                )
                return True
        
        logger.warning(f"[ FILE_PLAN ] No pending future found for token {token}")
        return False

    def resolve_tool_approval(self, token: str, decision: str, display_text: str = None):
        """
        Called by JS when the user resolves a generic tool-approval card
        (accept/decline or MCQ choice). Mirrors confirm_file_plan_approval but
        for the generic requires_approval primitive: sends the decision back
        to the Brain over the WebSocket, then submits a deterministic follow-up
        turn so the graph directly replays the checkpointed gated tool, then
        returns its output to the model (see src/infrastructure/tools/execution/executor_strategy.py).
        """
        window = self.main_window_ref()
        if not window:
            return False

        app = window.app_reference
        if not app or not hasattr(app, 'uplink_limb') or not app.uplink_limb:
            logger.warning("[ TOOL_APPROVAL ] No uplink_limb available to resolve approval")
            return False

        limb = app.uplink_limb

        async def _dispatch():
            from src.remote.protocol import ChadMessage, MessageType
            try:
                if limb.websocket:
                    msg = ChadMessage(MessageType.TOOL_APPROVAL_RESPONSE, {"token": token, "decision": decision})
                    await limb.websocket.send(msg.to_json())
                else:
                    logger.warning("[ TOOL_APPROVAL ] No active websocket to send resolution")
                    return

                # Persist approval resolution to message metadata in history repo if possible
                turn_id = getattr(limb, "current_turn_id", None)
                if turn_id and hasattr(limb, "history_repo") and limb.history_repo:
                    try:
                        await limb.history_repo.update_message_metadata(
                            turn_id,
                            {"actions": {"resolved": True, "userChoice": decision}}
                        )
                    except Exception as e:
                        logger.debug(f"[ TOOL_APPROVAL ] Failed updating message metadata: {e}")

                await limb.send_input(
                    text=display_text or decision,
                    conversation_id=limb.pending_conversation_id,
                )
            except Exception as e:
                logger.error(f"[ TOOL_APPROVAL ] Failed to resolve approval {token}: {e}")

        app.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_dispatch(), loop=app.loop))
        return True

    def reconnect_server(self):
        """Manually trigger a reconnection attempt to the Brain server."""
        logger.warning("[ BRIDGE ] Manual reconnect requested by user.")
        window = self.main_window_ref()
        if not (window and window.app_reference and window.app_reference.uplink_limb):
            return False

        # Immediate UI feedback — otherwise a hung AUTH wait makes Try again look dead.
        try:
            window.update_status(state="reconnecting", text="Trying to reconnect...")
            window.push_log("Manual reconnect requested.", "INFO")
        except Exception:
            pass

        limb = window.app_reference.uplink_limb
        app = window.app_reference
        loop = getattr(app, "loop", None)

        def _do_reconnect():
            limb.trigger_reconnect()

        if loop is not None:
            try:
                loop.call_soon_threadsafe(_do_reconnect)
                return True
            except Exception as e:
                logger.warning(f"[ BRIDGE ] Could not schedule reconnect on app loop: {e}")

        _do_reconnect()
        return True
