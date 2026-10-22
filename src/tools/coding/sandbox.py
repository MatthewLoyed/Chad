"""
E2B Sandbox Tools - Secure code execution and file generation with self-healing and auto-discovery.
"""

import os
from typing import Dict, Any, Optional, List
from src.tools.base_tool import BaseTool
from src.core.tools import failure
from src.shared.logger import setup_logger
from src.shared.config_manager import ConfigManager
from src.shared.file_registry import file_registry
from src.tools.display_helpers import format_sandbox_python, format_sandbox_bash
import base64
import difflib

logger = setup_logger("E2BSandbox")

# Tools that talk to the E2B cloud sandbox (used by LocalExecutor loop-breaker).
SANDBOX_TOOL_NAMES = frozenset({
    "run_sandboxed_bash",
    "run_sandboxed_python",
    "install_package",
    "download_sandbox_file",
})

# Default execution timeout in seconds for python scripts and bash commands.
# Feel free to increase this for extremely long-running operations.
EXECUTION_TIMEOUT = 300

# E2B sandboxes self-terminate after `SANDBOX_TTL` seconds of being idle. The default
# is ~300s, which silently kills the cached singleton between tool calls in a multi-step
# workflow ("The sandbox was not found" / 502). We create with a generous TTL and extend
# it on every reuse (keepalive) so a sandbox survives a full plan→execute loop.
SANDBOX_TTL = int(ConfigManager.get("tools.sandbox_ttl_seconds", 1800) or 1800)

# Singleton-like management of the sandbox instance
_sandbox_instance: Optional[Any] = None


def resolve_e2b_api_key() -> str:
    """Load and normalize E2B_API_KEY from secrets or environment.

    Prefer ``config/secrets.yaml`` (Settings) over ``.env``: both can hold an
    ``e2b_…``-shaped value, but the `.env` copy is often a stale leftover while
    Settings has the working key. ``get_secret`` already skips placeholder stubs
    and falls through to env when secrets is empty.
    """
    raw = ConfigManager.get_secret("E2B_API_KEY")
    if raw is None:
        raise ValueError(
            "Missing E2B_API_KEY. Add it to config/secrets.yaml (Settings) or .env "
            "to enable the code sandbox."
        )
    key = str(raw).strip().strip('"').strip("'")
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if not key:
        raise ValueError(
            "E2B_API_KEY is empty. Set a valid key from https://e2b.dev in "
            "config/secrets.yaml."
        )
    if not key.startswith("e2b_"):
        raise ValueError(
            "E2B_API_KEY looks invalid (expected a value starting with 'e2b_'). "
            "Remove extra quotes or a 'Bearer ' prefix in config/secrets.yaml."
        )
    return key


def _classify_sandbox_message(message: str) -> Optional[str]:
    """Domain matcher for E2B errors — registered with the shared classifier.

    Only the cases the generic patterns would get WRONG live here. A bare "502"
    from E2B means the sandbox died, not that the service is down, and a missing
    key surfaces as our own ValueError text rather than anything HTTP-shaped.
    """
    lower = message.lower()
    if (
        "missing e2b_api_key" in lower
        or "e2b_api_key is empty" in lower
        or "e2b_api_key looks invalid" in lower
    ):
        return failure.CONFIG
    if "sandbox was not found" in lower or "502" in message:
        return failure.EXPIRED
    return None


failure.register_classifier(_classify_sandbox_message)

# E2B-specific recovery text. The generic per-class actions are correct in shape
# but can't name the key, the file, or the dashboard — so these override them.
_SANDBOX_ACTIONS: Dict[str, list] = {
    failure.CONFIG: [
        "Do NOT retry any sandbox tool — it is not configured.",
        "Tell the user to add a valid E2B_API_KEY in Settings (get one at https://e2b.dev).",
        "Finish whatever part of the task does not need code execution.",
    ],
    failure.AUTH: [
        "E2B rejected the key. Do NOT retry run_sandboxed_bash/run_sandboxed_python.",
        "Tell the user to fix E2B_API_KEY in config/secrets.yaml and verify it at https://e2b.dev.",
    ],
    failure.EXPIRED: [
        "The sandbox session expired. Retry the SAME command once — it will be recreated.",
    ],
}


def sandbox_failure_response(error: Any, **extra: Any) -> Dict[str, Any]:
    """Structured tool failure for E2B sandbox tools (shared envelope)."""
    message = str(error)
    cls = failure.classify(message)
    return failure.failure(
        message,
        failure_class=cls,
        next_actions=_SANDBOX_ACTIONS.get(cls),
        **extra,
    )


def _new_sandbox() -> Any:
    from e2b_code_interpreter import Sandbox
    api_key = resolve_e2b_api_key()
    logger.info(f"[ E2B ] Initializing new Sandbox (ttl={SANDBOX_TTL}s)...")
    return Sandbox.create(api_key=api_key, timeout=SANDBOX_TTL)


def get_sandbox() -> Any:
    """Get or create a persistent, self-healing E2B sandbox.

    Reuses the cached instance only if it is still alive, extending its TTL
    (keepalive). A dead/expired sandbox is transparently replaced so callers
    never hit a stale "sandbox was not found" reference.
    """
    global _sandbox_instance
    if _sandbox_instance is not None:
        try:
            if _sandbox_instance.is_running():
                _sandbox_instance.set_timeout(SANDBOX_TTL)  # keepalive
                return _sandbox_instance
            logger.warning("[ E2B ] Cached sandbox no longer running — recreating.")
        except Exception as e:
            logger.warning(f"[ E2B ] Cached sandbox unhealthy ({e}) — recreating.")
        _sandbox_instance = None

    _sandbox_instance = _new_sandbox()
    return _sandbox_instance

def register_artifact_bytes(local_path: str, filename: str, content_bytes: bytes) -> Optional[str]:
    """Registers the file in the FileRegistry for UI download button support."""
    try:
        catchable_exts = {
            '.html': 'text/html',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.json': 'application/json',
            '.csv': 'text/csv',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg'
        }
        
        ext = os.path.splitext(filename)[1].lower()
        if ext not in catchable_exts:
            return None

        # Register the file using base64 encoded bytes
        encoded_data = base64.b64encode(content_bytes).decode('utf-8')
        token = file_registry.store_file(filename, encoded_data, catchable_exts[ext])
        
        logger.info("[ ARTIFACT ] Registered {filename} with token: {token}")
        return token

    except Exception as e:
        logger.warning(f"Artifact registration failed for {filename}: {e}")
        return None

def _get_files_snapshot(sb: Any) -> Dict[str, str]:
    """Helper to take a snapshot of filenames in common working directories of the sandbox."""
    snapshot = {}
    for path in ["/home/user", "/root", "/"]:
        try:
            for f in sb.files.list(path):
                if not f.is_dir:
                    snapshot[f.name] = f"{path}/{f.name}"
        except Exception:
            pass
    return snapshot

class ExecutePythonTool(BaseTool):
    """Tool to execute Python code in a secure E2B sandbox."""
    
    name = "run_sandboxed_python"
    description = "Execute Python code in a secure, isolated sandbox. Use this for data analysis, document generation (python-docx), or any complex logic. Returns stdout, stderr, and automatically downloads and delivers any newly created document formats (e.g., .docx, .xlsx, .pdf, .png)."
    category = "coding"
    requirements = ["E2B_API_KEY"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        return format_sandbox_python(params)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code to execute."
                    }
                },
                "required": ["code"]
            }
        }
    
    async def execute(self, code: str, **kwargs) -> Dict[str, Any]:
        try:
            sb = get_sandbox()
            logger.info(f"[ E2B ] Executing Python code...")
            
            # Take file snapshot before execution
            before_files = _get_files_snapshot(sb)
            
            execution = sb.run_code(code, timeout=EXECUTION_TIMEOUT)
            
            # Take file snapshot after execution and identify newly created files
            after_files = _get_files_snapshot(sb)
            new_files = {name: path for name, path in after_files.items() if name not in before_files}
            
            auto_downloaded = []
            auto_downloaded_msgs = []
            allowed_extensions = {'.docx', '.xlsx', '.pdf', '.png', '.csv', '.html', '.jpg', '.jpeg'}
            
            for name, path in new_files.items():
                ext = os.path.splitext(name)[1].lower()
                if ext in allowed_extensions:
                    try:
                        content = sb.files.read(path, format="bytes")
                        artifacts_dir = os.path.abspath("artifacts")
                        os.makedirs(artifacts_dir, exist_ok=True)
                        local_path = os.path.join(artifacts_dir, name)
                        
                        with open(local_path, "wb") as f:
                            f.write(content)
                        
                        token = register_artifact_bytes(local_path, name, content)
                        auto_downloaded.append({
                            "filename": name,
                            "local_path": local_path,
                            "token": token
                        })
                        auto_downloaded_msgs.append(f"Auto-delivered generated artifact to your local artifacts folder: {name}")
                    except Exception as download_err:
                        logger.warning(f"Auto-download failed for {name}: {download_err}")
            
            stdout = execution.logs.stdout or ""
            if auto_downloaded_msgs:
                notice = "\n\n" + "\n".join([f"[ SYSTEM NOTICE ] {msg}" for msg in auto_downloaded_msgs])
                stdout += notice
                
            return {
                "success": True,
                "stdout": stdout,
                "stderr": execution.logs.stderr,
                "results": [str(r) for r in execution.results],
                "error": execution.error if execution.error else None,
                "auto_delivered_artifacts": auto_downloaded if auto_downloaded else None
            }
        except Exception as e:
            logger.error(f"E2B Python Execution failed: {e}")
            return sandbox_failure_response(e)

class InstallPackageTool(BaseTool):
    """Tool to install pip packages in the E2B sandbox."""
    
    name = "install_package"
    description = "Install a Python package (pip) inside the sandbox. Use this before running code that requires external libraries (e.g., 'python-docx', 'pandas', 'matplotlib')."
    category = "coding"
    requirements = ["E2B_API_KEY"]
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "The name of the package to install (e.g., 'python-docx')."
                    }
                },
                "required": ["package"]
            }
        }
    
    async def execute(self, package: str, **kwargs) -> Dict[str, Any]:
        try:
            sb = get_sandbox()
            logger.info(f"[ E2B ] Installing package: {package}")
            result = sb.run_code(f"!pip install {package}", timeout=EXECUTION_TIMEOUT)
            
            return {
                "success": True,
                "result": f"Installed {package}",
                "stdout": result.logs.stdout,
                "stderr": result.logs.stderr
            }
        except Exception as e:
            logger.error(f"E2B Install failed: {e}")
            return sandbox_failure_response(e)

class DownloadSandboxFileTool(BaseTool):
    """Tool to download a file from the E2B sandbox to the local system with self-healing."""
    
    name = "download_sandbox_file"
    description = "Download a file generated inside the sandbox to the local host. Features self-healing: if the exact filename is misspelled, it automatically fuzzy-matches and downloads the closest matching file."
    category = "coding"
    requirements = ["E2B_API_KEY"]
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "sandbox_path": {
                        "type": "string",
                        "description": "Path to the file inside the sandbox (e.g., '/home/user/output.docx')."
                    },
                    "local_filename": {
                        "type": "string",
                        "description": "Name to save the file as locally (saved under the project artifacts/ folder)."
                    }
                },
                "required": ["sandbox_path", "local_filename"]
            }
        }
    
    async def execute(self, sandbox_path: str, local_filename: str, **kwargs) -> Dict[str, Any]:
        try:
            sb = get_sandbox()
            
            # --- Self-healing Path Resolution ---
            real_path = sandbox_path
            resolved_filename = local_filename
            exists = False
            
            # 1. Try exact path
            try:
                sb.files.read(real_path, format="bytes")
                exists = True
            except Exception:
                pass
                
            # 2. Self-heal if not found
            if not exists:
                logger.info(f"[ E2B ] Exact path '{sandbox_path}' not found. Initiating self-healing...")
                basename = os.path.basename(sandbox_path)
                name_without_ext, ext = os.path.splitext(basename)
                
                # Check target directory
                search_dir = os.path.dirname(sandbox_path)
                if not search_dir or search_dir == "":
                    search_dir = "/home/user"
                    
                try:
                    candidates = sb.files.list(search_dir)
                    files_only = [f for f in candidates if not f.is_dir]
                    
                    # Fuzzy match on basename (case-insensitive)
                    for f in files_only:
                        if f.name.lower() == basename.lower():
                            real_path = f"{search_dir}/{f.name}"
                            resolved_filename = f.name
                            exists = True
                            logger.info(f"[ E2B ] Self-healed: Case-insensitive match resolved to '{real_path}'")
                            break
                            
                    # Fuzzy match on extension if single file of that type exists
                    if not exists and ext:
                        same_ext_files = [f for f in files_only if os.path.splitext(f.name)[1].lower() == ext.lower()]
                        if len(same_ext_files) == 1:
                            real_path = f"{search_dir}/{same_ext_files[0].name}"
                            resolved_filename = same_ext_files[0].name
                            exists = True
                            logger.info(f"[ E2B ] Self-healed: Resolved single matching '{ext}' file to '{real_path}'")
                        elif len(same_ext_files) > 1:
                            # Fuzzy string match close matches
                            matches = difflib.get_close_matches(basename, [f.name for f in same_ext_files], n=1, cutoff=0.1)
                            if matches:
                                real_path = f"{search_dir}/{matches[0]}"
                                resolved_filename = matches[0]
                                exists = True
                                logger.info(f"[ E2B ] Self-healed: Closest name fuzzy match resolved to '{real_path}'")
                except Exception as list_err:
                    logger.warning(f"Self-healing directory scanning failed: {list_err}")
            
            # --- Perform Download ---
            logger.info(f"[ E2B ] Downloading file from sandbox: {real_path}")
            content = sb.files.read(real_path, format="bytes")
            
            # Save to local artifacts folder
            artifacts_dir = os.path.abspath("artifacts")
            os.makedirs(artifacts_dir, exist_ok=True)
            local_path = os.path.join(artifacts_dir, resolved_filename)
            
            with open(local_path, "wb") as f:
                f.write(content)
            
            # Apply artifact catcher for the UI Download button
            token = register_artifact_bytes(local_path, resolved_filename, content)
            
            return {
                "success": True,
                "result": f"Successfully downloaded to {local_path}" + (f" (Self-healed from {sandbox_path})" if real_path != sandbox_path else ""),
                "download_token": token,
                "local_path": local_path,
                "filename": resolved_filename
            }
        except Exception as e:
            logger.error(f"E2B Download failed: {e}")
            return sandbox_failure_response(
                f"File '{sandbox_path}' not found and self-healing failed. Details: {e}"
            )

class ExecuteBashTool(BaseTool):
    """Tool to execute bash commands in the E2B sandbox."""
    
    name = "run_sandboxed_bash"
    description = "Execute a bash command in the secure E2B sandbox. Use this for file management, shell scripts, or advanced OS tasks within the sandbox. Automatically downloads and delivers newly created document formats."
    category = "coding"
    requirements = ["E2B_API_KEY"]
    
    def format_action(self, params: Dict[str, Any]) -> str:
        return format_sandbox_bash(params)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute."
                    }
                },
                "required": ["command"]
            }
        }
    
    async def execute(self, command: str, **kwargs) -> Dict[str, Any]:
        try:
            sb = get_sandbox()
            logger.info(f"[ E2B ] Executing bash command: {command}")
            
            # Take file snapshot before execution
            before_files = _get_files_snapshot(sb)
            
            result = sb.commands.run(command, timeout=EXECUTION_TIMEOUT)
            
            # Take file snapshot after execution and identify newly created files
            after_files = _get_files_snapshot(sb)
            new_files = {name: path for name, path in after_files.items() if name not in before_files}
            
            auto_downloaded = []
            auto_downloaded_msgs = []
            allowed_extensions = {'.docx', '.xlsx', '.pdf', '.png', '.csv', '.html', '.jpg', '.jpeg'}
            
            for name, path in new_files.items():
                ext = os.path.splitext(name)[1].lower()
                if ext in allowed_extensions:
                    try:
                        content = sb.files.read(path, format="bytes")
                        artifacts_dir = os.path.abspath("artifacts")
                        os.makedirs(artifacts_dir, exist_ok=True)
                        local_path = os.path.join(artifacts_dir, name)
                        
                        with open(local_path, "wb") as f:
                            f.write(content)
                        
                        token = register_artifact_bytes(local_path, name, content)
                        auto_downloaded.append({
                            "filename": name,
                            "local_path": local_path,
                            "token": token
                        })
                        auto_downloaded_msgs.append(f"Auto-delivered generated artifact to your local artifacts folder: {name}")
                    except Exception as download_err:
                        logger.warning(f"Auto-download failed for {name}: {download_err}")
            
            stdout = result.stdout or ""
            if auto_downloaded_msgs:
                notice = "\n\n" + "\n".join([f"[ SYSTEM NOTICE ] {msg}" for msg in auto_downloaded_msgs])
                stdout += notice
                
            return {
                "success": True,
                "stdout": stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "auto_delivered_artifacts": auto_downloaded if auto_downloaded else None
            }
        except Exception as e:
            logger.error(f"E2B Bash Execution failed: {e}")
            return sandbox_failure_response(e)
