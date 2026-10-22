"""
Propose File Plan Tool - Plan-Before-Action Filesystem Operations

For batch organize/move/rename/delete of an existing file tree — NOT for creating
a single deliverable (use write_file / build_html_file → artifacts/ instead).
Prescribing absolute output paths in prompts used to drag models into
file_manager.create_folder → this tool's approval gate; that was a prompt smell.

Chad calls this tool before any batch file operation. It formats the plan
into a human-readable summary and generates a cryptographic approval token.

DESIGN PRINCIPLES:
1. DETERMINISTIC: Token-based approval cannot be bypassed by the LLM
2. TRANSPARENT: Opens the target folder so user can visually verify
3. EXPLICIT: User must provide the token code to approve
"""

import uuid
import json
import os
import subprocess
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime, timedelta

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

# Import the shared approval registry from file_manager
from src.tools.filesystem.file_manager import (
    generate_approval_token,
    register_approval,
    _pending_approvals
)

logger = setup_logger("ProposeFilePlan")


class ProposeFilePlanTool(BaseTool):
    """
    Planning tool for batch filesystem operations.
    
    Chad calls this BEFORE executing any batch file operation.
    The tool formats the plan, generates an approval token, and
    the user must confirm by providing the token.
    """
    
    name = "propose_file_plan"
    description = (
        "REQUIRED before batch organize/move/rename/delete of existing files. "
        "Not for writing a new artifact — use write_file or build_html_file instead. "
        "Returns a markdown plan and approval token. "
        "Workflow: 1. Call with plan. 2. Show result to user. 3. Wait for approval code. 4. Pass code to execute."
    )
    category = "filesystem"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "target_folder": {
                        "type": "string",
                        "description": "Root folder."
                    },
                    "plan_summary": {
                        "type": "string",
                        "description": "High-level goal."
                    },
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["move", "rename", "create_folder"]},
                                "source": {"type": "string", "description": "Filename (no wildcards)."},
                                "destination": {"type": "string"}
                            }
                        }
                    }
                },
                "required": ["target_folder", "plan_summary", "actions"]
            }
        }
    
    async def execute(
        self, 
        target_folder: str, 
        plan_summary: str, 
        actions: List[Dict[str, Any]],
        **kwargs
    ) -> Dict[str, Any]:
        import asyncio
        
        # 1. Generate cryptographic approval token
        approval_token = generate_approval_token()
        plan_id = str(uuid.uuid4())[:8]  # Internal reference

        # 2. Open the folder immediately so user can see context
        try:
            # Resolve path properly
            path = Path(os.path.expanduser(os.path.expandvars(target_folder))).resolve()
            if path.exists() and path.is_dir():
                subprocess.Popen(["explorer", str(path)])
            else:
                # Fallback: try to see if it's relative to user profile
                user_path = Path(os.path.expanduser(f"~/{target_folder}")).resolve()
                if user_path.exists():
                    subprocess.Popen(["explorer", str(user_path)])
                    path = user_path  # Update for later use
        except Exception as e:
            logger.error(f"Failed to auto-open folder: {e}")

        if not actions:
            return {"success": False, "error": "No actions provided in the plan."}

        # 3. Group actions by type for display
        new_folders = []
        moves = []
        renames = []
        deletes = []
        
        # Helper to clean up paths for display (remove redundant root)
        def clean_path(p: str, root: str) -> str:
            p_str = str(p).replace("\\", "/")
            root_str = str(root).replace("\\", "/")
            if p_str.startswith(root_str):
                return p_str[len(root_str):].lstrip("/")
            return p_str

        for action in actions:
            action_type = action.get("action", "unknown")
            source = action.get("source", "?")
            dest = action.get("destination", "")
            
            # Clean paths for display
            display_source = clean_path(source, target_folder)
            display_dest = clean_path(dest, target_folder)

            if action_type == "create_folder":
                new_folders.append(f"- `+` **{display_dest or display_source}**")
            elif action_type == "move":
                moves.append(f"- `{display_source}` → `{display_dest}`")
            elif action_type == "rename":
                renames.append(f"- `{display_source}` → `{display_dest}`")
            elif action_type == "delete":
                logger.warning(f"BLOCKED: ProposeFilePlan received a 'delete' action for {display_source}. Deletion is disabled.")
                continue  # Skip delete actions entirely

        # 4. Build formatted output - SUMMARY FIRST approach
        # instead of listing every file, we aggregate by extension/category
        
        # Calculate stats for the summary
        from collections import defaultdict
        
        move_stats = defaultdict(int)  # e.g., {'.png': 15, '.pdf': 5}
        folder_stats = len(new_folders)
        total_moves = 0
        
        for action in actions:
            if action.get("action") == "move":
                src = action.get("source", "")
                ext = Path(src).suffix.lower() or "files"
                move_stats[ext] += 1
                total_moves += 1
                
        # Group stats by category (simplified)
        categories = defaultdict(int)
        cat_map = {
            '.png': '📸 Images', '.jpg': '📸 Images', '.jpeg': '📸 Images', '.gif': '📸 Images', '.webp': '📸 Images',
            '.mp4': '🎥 Video', '.mov': '🎥 Video', '.avi': '🎥 Video', '.mkv': '🎥 Video',
            '.mp3': '🎵 Audio', '.wav': '🎵 Audio', '.flac': '🎵 Audio',
            '.pdf': '📄 Docs', '.docx': '📄 Docs', '.txt': '📄 Docs', '.md': '📄 Docs', '.xls': '📊 Sheets', '.csv': '📊 Sheets',
            '.zip': '📦 Archives', '.rar': '📦 Archives', '.7z': '📦 Archives',
            '.exe': '💾 Apps', '.msi': '💾 Apps', '.dmg': '💾 Apps',
            '.py': '👨‍💻 Code', '.js': '👨‍💻 Code', '.ts': '👨‍💻 Code', '.html': '👨‍💻 Code', '.css': '👨‍💻 Code', '.json': '👨‍💻 Code'
        }
        
        unknown_cat_count = 0
        for ext, count in move_stats.items():
            cat = cat_map.get(ext)
            if cat:
                categories[cat] += count
            else:
                unknown_cat_count += count
                
        # Build the Markdown Summary
        output_parts = [f"## 📂 File Organization Plan\n**Goal:** {plan_summary}\n"]
        output_parts.append(f"Moving **{total_moves} files** into **{folder_stats} new folders**.\n")
        
        if categories:
            output_parts.append("### 📊 Summary")
            for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
                output_parts.append(f"- {cat}: {count} files")
            
        if unknown_cat_count > 0:
            output_parts.append(f"- 📁 Other: {unknown_cat_count} files")
            
        output_parts.append("")
        output_parts.append("> [!TIP]")
        output_parts.append("> Click **Approve** to execute this plan instantly.")
        
        approval_message = "\n".join(output_parts)

        # 5. Calculate summary statistics
        action_counts = {}
        for action in actions:
            action_type = action.get("action", "unknown")
            action_counts[action_type] = action_counts.get(action_type, 0) + 1
            
        action_summary_parts = []
        for action_type, count in action_counts.items():
            action_summary_parts.append(f"{count} {action_type}(s)")
        action_summary = ", ".join(action_summary_parts)

        # 6. Register the approval token with the plan
        register_approval(approval_token, {
            "plan_id": plan_id,
            "target_folder": str(path) if 'path' in dir() else target_folder,
            "plan_summary": plan_summary,
            "actions": actions,
        })
        
        logger.info(f"[ PROPOSE_FILE_PLAN ] Created plan {plan_id} with token {approval_token}")
        
        # 7. Push plan to UI with inline approval buttons
        app = kwargs.get('_app_reference')
        if app:
            try:
                # Create a Future to await user response
                response_future = app.loop.create_future()
                
                # Store the future keyed by token
                if not hasattr(app, '_file_plan_futures'):
                    app._file_plan_futures = {}
                app._file_plan_futures[approval_token] = response_future
                
                # Push to UI via JavaScript
                # FIX: Import inside method to avoid circular imports
                # FIX: Access 'window' attribute, not 'main_window'
                window = getattr(app, 'window', getattr(app, 'main_window', None))
                
                logger.debug(f"[ PROPOSE_FILE_PLAN ] App reference found: {app}")
                logger.debug(f"[ PROPOSE_FILE_PLAN ] Window found: {window}")
                
                if window:
                    target = getattr(window, 'webview_window', window)
                    logger.debug(f"[ PROPOSE_FILE_PLAN ] Webview Target: {target}")
                    
                    if hasattr(target, 'evaluate_js'):
                        escaped_message = json.dumps(approval_message)
                        escaped_token = json.dumps(approval_token)
                        js_code = f"window.addFilePlanToChat({escaped_message}, {escaped_token})"
                        logger.debug(f"[ PROPOSE_FILE_PLAN ] Executing JS: {js_code[:100]}...")
                        
                        target.evaluate_js(js_code)
                        logger.info(f"[ PROPOSE_FILE_PLAN ] Pushed plan to UI, awaiting user response...")
                        
                        # Wait for user response (timeout 5 minutes, check for interrupts every second)
                        try:
                            # Poll with short timeouts to allow interrupt checking
                            timeout_remaining = 300.0  # 5 minutes total
                            poll_interval = 1.0  # Check every second
                            
                            while timeout_remaining > 0:
                                try:
                                    user_approved = await asyncio.wait_for(
                                        asyncio.shield(response_future), 
                                        timeout=poll_interval
                                    )
                                    # Got a response!
                                    break
                                except asyncio.TimeoutError:
                                    timeout_remaining -= poll_interval
                                    
                                    # Check if task was cancelled (interrupt)
                                    if asyncio.current_task().cancelled():
                                        logger.warning(f"[ PROPOSE_FILE_PLAN ] Task cancelled - cleaning up")
                                        app._file_plan_futures.pop(approval_token, None)
                                        return {
                                            "success": False,
                                            "error": "Plan approval was interrupted.",
                                            "interrupted": True,
                                            "caption": "⚠️ Interrupted"
                                        }
                                    
                                    # Continue waiting
                                    continue
                            else:
                                # Loop completed without response = timeout
                                raise asyncio.TimeoutError()
                            
                            # Cleanup
                            app._file_plan_futures.pop(approval_token, None)
                            
                            if user_approved:
                                # Auto-execute the approved plan
                                logger.info(f"[ PROPOSE_FILE_PLAN ] User approved - executing plan...")
                                executor = ExecuteApprovedPlanTool(self.config)
                                exec_result = await executor.execute(approval_token=approval_token)
                                return {
                                    "success": exec_result.get("success", False),
                                    "result": f"Plan approved and executed. {exec_result.get('result', '')}",
                                    "execution_details": exec_result,
                                    "caption": f"✅ Executed: {action_summary}"
                                }
                            else:
                                logger.info(f"[ PROPOSE_FILE_PLAN ] User rejected plan")
                                return {
                                    "success": True,
                                    "result": "Plan was rejected by user. No changes were made.",
                                    "rejected": True,
                                    "caption": "❌ Plan rejected"
                                }
                                
                        except asyncio.TimeoutError:
                            logger.warning(f"[ PROPOSE_FILE_PLAN ] Approval timed out for token {approval_token}")
                            app._file_plan_futures.pop(approval_token, None)
                            return {
                                "success": False,
                                "error": "Plan approval timed out (5 minutes). Please try again.",
                                "approval_token": approval_token,  # Return token so user can still approve manually
                                "caption": "⏱️ Approval timed out"
                            }
                        except asyncio.CancelledError:
                            logger.warning(f"[ PROPOSE_FILE_PLAN ] Task cancelled during approval wait")
                            app._file_plan_futures.pop(approval_token, None)
                            return {
                                "success": False,
                                "error": "Plan approval was cancelled.",
                                "interrupted": True,
                                "caption": "⚠️ Cancelled"
                            }
                    else:
                        logger.error("[ PROPOSE_FILE_PLAN ] Target window has no evaluate_js method")
                else:
                    logger.error("[ PROPOSE_FILE_PLAN ] Window object not found on app reference")
                            
            except Exception as e:
                logger.error(f"[ PROPOSE_FILE_PLAN ] Error pushing to UI: {e}", exc_info=True)
        else:
            logger.error("[ PROPOSE_FILE_PLAN ] No app_reference provided in kwargs - cannot push to UI")
        
        # 8. Fallback: Return the formatted plan if UI integration not available
        # If we reached here, it means UI push failed or app_reference was missing.
        # We must provide the token so the user can approve manually.
        
        fallback_instruction = (
            f"\n\n> [!WARNING]\n"
            f"> **UI Buttons Failed to Load**\n"
            f"> To approve this plan manually, reply with:\n"
            f"> `execute plan {approval_token}`"
        )
        
        return {
            "success": True,
            "result": approval_message + fallback_instruction,
            "approval_token": approval_token,
            "plan_id": plan_id,
            "action_count": len(actions),
            "action_summary": action_summary,
            "requires_approval": True,
            "caption": f"Plan: {action_summary} (Token: {approval_token})"
        }
    
    def validate_params(
        self, 
        target_folder: str = None, 
        plan_summary: str = None, 
        actions: List = None,
        **kwargs
    ) -> tuple[bool, Optional[str]]:
        if not target_folder:
            return False, "target_folder is required"
        if not plan_summary:
            return False, "plan_summary is required"
        if not actions or not isinstance(actions, list):
            return False, "actions must be a non-empty list"
        return True, None


class ExecuteApprovedPlanTool(BaseTool):
    """
    Execute a previously approved file plan.
    
    This is a convenience tool that executes all actions in sequence
    using the file_manager tool, but ONLY if the user has provided
    the correct approval token.
    """
    
    name = "execute_approved_plan"
    description = (
        "Execute a file plan after user approval. "
        "Pass the approval_token that the user provided after seeing the plan."
    )
    category = "filesystem"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        # We'll need access to file_manager to execute operations
        from src.tools.filesystem.file_manager import FileManagerTool
        self.file_manager = FileManagerTool(config)
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "approval_token": {
                        "type": "string",
                        "description": "The approval token provided by the user (e.g., 'CHAD-A7X9')."
                    },
                    "open_folder_first": {
                        "type": "boolean",
                        "description": "If true, open the target folder in File Explorer before executing (default: true)."
                    }
                },
                "required": ["approval_token"]
            }
        }
    
    async def execute(
        self, 
        approval_token: str, 
        open_folder_first: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """Execute all actions in an approved plan."""
        
        # Import consume function
        from src.tools.filesystem.file_manager import consume_approval
        
        # Validate and consume the token
        plan = consume_approval(approval_token)
        if not plan:
            return {
                "success": False, 
                "error": f"Invalid or expired approval token '{approval_token}'. "
                         "Please get a new plan from propose_file_plan."
            }
        
        actions = plan["actions"]
        target_folder = plan["target_folder"]
        
        logger.info(f"[ EXECUTE_PLAN ] Executing plan with token {approval_token}, {len(actions)} actions")
        
        # Open folder first so user can watch
        if open_folder_first:
            await self.file_manager._open_folder(target_folder)
        
        # Execute each action (passing a dummy token since we already consumed it)
        # Actually, re-register the token temporarily for each action
        from src.tools.filesystem.file_manager import register_approval
        register_approval(approval_token, plan, expires_minutes=10)  # Re-register for execution
        
        results = []
        successful = 0
        failed = 0
        
        for action in actions:
            action_type = action.get("action")
            source = action.get("source")
            destination = action.get("destination")
            
            # Call the internal methods directly (they don't need token since we validated)
            if action_type == "move":
                result = await self.file_manager._move_file(source, destination, approval_token)
            elif action_type == "rename":
                result = await self.file_manager._rename_file(source, destination, approval_token)
            elif action_type == "delete":
                result = await self.file_manager._delete_file(source, approval_token)
            elif action_type == "create_folder":
                result = await self.file_manager._create_folder(destination or source, approval_token)
            else:
                result = {"success": False, "error": f"Unknown action: {action_type}"}
            
            results.append(result)
            if result.get("success"):
                successful += 1
            else:
                failed += 1
        
        # Clean up the re-registered token
        _pending_approvals.pop(approval_token, None)
        
        summary = f"Completed {successful}/{len(actions)} operations"
        if failed > 0:
            summary += f" ({failed} failed)"
        
        return {
            "success": failed == 0,
            "result": summary,
            "successful": successful,
            "failed": failed,
            "details": results,
            "caption": summary
        }
    
    def validate_params(self, approval_token: str = None, **kwargs) -> tuple[bool, Optional[str]]:
        if not approval_token:
            return False, "approval_token is required"
        return True, None
