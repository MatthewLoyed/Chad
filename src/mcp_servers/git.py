"""
chad-git - Git MCP Server for CHAD
Provides safe, robust, and audited Git version control capabilities inside the active repository.
"""

import os
import sys
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
import asyncio

from mcp.server.fastmcp import FastMCP

from src.shared.logger import setup_logger

logger = setup_logger("chad-git-server")

# Initialize the FastMCP server
mcp = FastMCP("chad-git")

# Helper to run shell git commands securely in the current workspace directory
async def _run_git_command(args: List[str]) -> tuple[bool, str, str]:
    """Execute a git command asynchronously in the workspace directory."""
    cwd = os.getcwd()
    try:
        cmd = ["git"] + args
        logger.info(f"[ GIT EXECUTE ] Running: {' '.join(cmd)} in {cwd}")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )
        stdout, stderr = await process.communicate()
        
        stdout_str = stdout.decode('utf-8', errors='replace').strip()
        stderr_str = stderr.decode('utf-8', errors='replace').strip()
        success = process.returncode == 0
        
        return success, stdout_str, stderr_str
    except Exception as e:
        logger.error(f"Git execution failed for args {args}: {e}", exc_info=True)
        return False, "", f"Execution error: {str(e)}"


# ============================================================================
# READ-CLASS GIT TOOLS (AUTO-EXECUTE)
# ============================================================================

@mcp.tool(meta={"hitl_class": "READ"})
async def git_status() -> Dict[str, Any]:
    """
    Get the status of files in the current git repository.
    Shows untracked, modified, and staged changes.
    """
    success, stdout, stderr = await _run_git_command(["status", "-u"])
    if not success:
        return {"success": False, "error": stderr or "Failed to retrieve git status"}
        
    return {
        "success": True,
        "result": "Successfully fetched git status",
        "output": stdout
    }


@mcp.tool(meta={"hitl_class": "READ"})
async def git_diff(path: Optional[str] = None, cached: bool = False) -> Dict[str, Any]:
    """
    Retrieve git diff information for changes.
    
    Args:
        path: Optional file path to scope the diff to.
        cached: Set to True to show staged changes (git diff --cached).
    """
    args = ["diff"]
    if cached:
        args.append("--cached")
    if path:
        args.append(path)
        
    success, stdout, stderr = await _run_git_command(args)
    if not success:
        return {"success": False, "error": stderr or "Failed to run git diff"}
        
    truncated = False
    display_diff = stdout
    if len(stdout) > 8000:
        display_diff = stdout[:8000] + "\n\n...[DIFF TRUNCATED FOR SIZE]..."
        truncated = True
        
    return {
        "success": True,
        "result": f"Fetched git diff{' (cached)' if cached else ''}",
        "diff": display_diff,
        "truncated": truncated,
        "total_lines": stdout.count('\n') + 1 if stdout else 0
    }


@mcp.tool(meta={"hitl_class": "READ"})
async def git_log(limit: int = 5) -> Dict[str, Any]:
    """
    Get recent commits from the repository history.
    
    Args:
        limit: Maximum number of commits to list. Default is 5.
    """
    # Restrict limit safety bounds
    limit = max(1, min(limit, 30))
    
    success, stdout, stderr = await _run_git_command(["log", f"-n", str(limit), "--oneline"])
    if not success:
        return {"success": False, "error": stderr or "Failed to retrieve git log"}
        
    return {
        "success": True,
        "result": f"Fetched recent {limit} commits",
        "output": stdout
    }


# ============================================================================
# WRITE-CLASS GIT TOOLS (GATED EXECUTION)
# ============================================================================

@mcp.tool(meta={"hitl_class": "WRITE"})
async def git_commit(message: str, files: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Stage changes and commit them with a clean descriptive commit message.
    
    Args:
        message: The commit message (please follow Conventional Commits format).
        files: Optional list of specific files to commit. If empty or None, commits all tracked changes.
    """
    if not message.strip():
        return {"success": False, "error": "Commit message cannot be empty."}
        
    # Step 1: Stage files if specified
    if files:
        for f in files:
            stage_success, stage_out, stage_err = await _run_git_command(["add", f])
            if not stage_success:
                return {"success": False, "error": f"Failed to stage file '{f}': {stage_err}"}
                
    # Step 2: Commit
    commit_args = ["commit", "-m", message]
    if not files:
        # Default to committing all tracked changes if no files list given
        commit_args.insert(1, "-a")
        
    success, stdout, stderr = await _run_git_command(commit_args)
    if not success:
        return {"success": False, "error": stderr or "Failed to commit changes. Are there changes staged?"}
        
    return {
        "success": True,
        "result": f"Committed successfully: {message}",
        "output": stdout
    }


@mcp.tool(meta={"hitl_class": "WRITE"})
async def git_push_files() -> Dict[str, Any]:
    """
    Push local branch commits to the remote repository.
    """
    success, stdout, stderr = await _run_git_command(["push"])
    if not success:
        return {"success": False, "error": stderr or "Failed to push commits to remote."}
        
    return {
        "success": True,
        "result": "Successfully pushed commits to remote repository",
        "output": stdout or stderr
    }


@mcp.tool(meta={"hitl_class": "WRITE"})
async def git_checkout(branch: str, create: bool = False, from_branch: Optional[str] = None) -> Dict[str, Any]:
    """
    Switch branches or create a new branch.
    
    Args:
        branch: The name of the branch to switch to or create.
        create: Set to True to create a new branch (git checkout -b).
        from_branch: Optional source branch when creating a new one.
    """
    args = ["checkout"]
    if create:
        args.append("-b")
    args.append(branch)
    if from_branch and create:
        args.append(from_branch)
        
    success, stdout, stderr = await _run_git_command(args)
    if not success:
        return {"success": False, "error": stderr or f"Failed to checkout branch '{branch}'"}
        
    return {
        "success": True,
        "result": f"Switched to branch '{branch}'" + (" (created)" if create else ""),
        "output": stdout or stderr
    }


@mcp.tool(meta={"hitl_class": "WRITE"})
async def git_reset(mode: str = "mixed", path: Optional[str] = None) -> Dict[str, Any]:
    """
    Reset changes or stage status.
    
    Args:
        mode: Reset mode: 'soft', 'mixed', or 'hard'. Default is 'mixed'.
        path: Optional file path to reset staging for.
    """
    if mode not in ["soft", "mixed", "hard"]:
        return {"success": False, "error": f"Invalid reset mode: '{mode}'. Must be soft, mixed, or hard."}
        
    args = ["reset"]
    if path:
        # File reset is always mixed/unstaging in git
        args.append(path)
    else:
        args.append(f"--{mode}")
        
    success, stdout, stderr = await _run_git_command(args)
    if not success:
        return {"success": False, "error": stderr or "Failed to reset"}
        
    return {
        "success": True,
        "result": f"Successfully performed git reset {('--' + mode) if not path else path}",
        "output": stdout or stderr
    }


if __name__ == "__main__":
    mcp.run()
