"""Clone-provisioning pipeline for behavioral self-evals (issue #596, epic #358).

The Chad-on-Chad self-eval loop (docs/architecture/CHAD_SELF_EVAL_LOOP.md) hands Chad Code a
git worktree clone to edit. For the clone's behavior to be attributable to the fix rather than
environment drift, four things have to be true of it — and until this module, each was a manual
step done once during the #358 proof run:

  1. **Pinned to the eval's SHA** — a plain `dev` checkout drifts ahead of the commit the eval
     ran against, so before/after traces aren't comparable.
  2. **Has the `config/persona` overlay** — the rich live prompts win at runtime (see
     docs/architecture/PERSONA_PROMPTS.md); a bare checkout only has the generic baseline in
     `prompts/persona`, which is a *different, degraded* Chad.
  3. **Allowlisted for Chad Code to write into** — `openhands_workspace_allowlist` defaults to
     `[]` (a git repo is refused by design, #576) and this clone IS a git repo.
  4. **Zero secrets inside it** — the coder container must never see real tokens. The in-process
     eval reads secrets from OUTSIDE the clone via `CHAD_SECRETS_PATH` (config_manager.py).

`provision_eval_clone` does all four in one call. It never touches config.yaml; the allowlist
grant is a per-process env var consumed by `openhands_dispatch._workspace_guard`, so a fresh
throwaway eval clone doesn't loosen the permanent default for every other coding job.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from src.shared.logger import setup_logger
from src.tools.dev.openhands_dispatch import EXTRA_ALLOWLIST_ENV

logger = setup_logger("EvalCloneProvisioner")

_GIT_TIMEOUT_S = 60.0


def _repo_root() -> Path:
    # <root>/src/tools/dev/eval_clone_provisioner.py -> parents[3] == <root>
    return Path(__file__).resolve().parents[3]


def _default_clones_root() -> Path:
    import tempfile
    return Path(tempfile.gettempdir()) / "chad-code-eval-clones"


def _run_git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "git executable not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"git command timed out after {_GIT_TIMEOUT_S}s"


def _resolve_sha(sha: str, *, source_repo: Path) -> Optional[str]:
    """Full commit hash for `sha`, fetching it from origin if the local repo doesn't have it
    yet (a coder clone may be provisioned for a SHA from a branch nobody has fetched locally)."""
    rc, out, _ = _run_git(["rev-parse", "--verify", f"{sha}^{{commit}}"], cwd=source_repo)
    if rc != 0:
        _run_git(["fetch", "origin", sha], cwd=source_repo)
        rc, out, _ = _run_git(["rev-parse", "--verify", f"{sha}^{{commit}}"], cwd=source_repo)
    return out.strip() if rc == 0 else None


def _worktree_sha(dest: Path) -> Optional[str]:
    rc, out, _ = _run_git(["rev-parse", "HEAD"], cwd=dest)
    return out.strip() if rc == 0 else None


def provision_eval_clone(
    sha: str,
    *,
    job_id: Optional[str] = None,
    clones_root: Optional[Path] = None,
    source_repo: Optional[Path] = None,
) -> Dict[str, Any]:
    """Provision a SHA-pinned, persona-complete, allowlisted, secrets-free clone for Chad Code to
    edit as part of a behavioral self-eval. Idempotent: re-provisioning the same `job_id` reuses
    the existing worktree if it's already at `sha`, otherwise recreates it.

    Returns:
        {success, path, sha, persona_copied, allowlist_env, secrets_env} on success — merge
        `allowlist_env`/`secrets_env` into the environment of the process that dispatches Chad
        Code / runs the in-process eval against this clone.
        {success: False, error} on failure.
    """
    source_repo = (source_repo or _repo_root()).resolve()
    clones_root = (clones_root or _default_clones_root()).resolve()
    job_id = job_id or uuid.uuid4().hex[:8]
    dest = clones_root / job_id

    resolved_sha = _resolve_sha(sha, source_repo=source_repo)
    if not resolved_sha:
        return {"success": False, "error": f"SHA '{sha}' not found locally and could not be "
                f"fetched from origin (source repo: {source_repo})."}

    if dest.exists():
        if _worktree_sha(dest) == resolved_sha:
            logger.info(f"[ EvalClone ] reusing existing clone at {dest} (already at {resolved_sha[:8]})")
        else:
            _run_git(["worktree", "remove", "--force", str(dest)], cwd=source_repo)
            shutil.rmtree(dest, ignore_errors=True)
    if not dest.exists():
        clones_root.mkdir(parents=True, exist_ok=True)
        rc, out, err = _run_git(
            ["worktree", "add", "--detach", str(dest), resolved_sha], cwd=source_repo,
        )
        if rc != 0:
            return {"success": False, "error": f"git worktree add failed: {err.strip() or out.strip()}"}

    persona_src = source_repo / "config" / "persona"
    persona_copied = False
    if persona_src.is_dir():
        shutil.copytree(persona_src, dest / "config" / "persona", dirs_exist_ok=True)
        persona_copied = True

    secrets_path = source_repo / "config" / "secrets.yaml"

    logger.info(f"[ EvalClone ] provisioned {dest} @ {resolved_sha[:8]} "
                f"(persona_copied={persona_copied})")
    return {
        "success": True,
        "path": str(dest),
        "sha": resolved_sha,
        "job_id": job_id,
        "persona_copied": persona_copied,
        # Merge into the dispatching process's env: grants Chad Code write access to this one
        # clone without touching config.yaml's permanent openhands_workspace_allowlist.
        "allowlist_env": {EXTRA_ALLOWLIST_ENV: str(dest)},
        # Merge into the in-process eval's env when running with cwd=<clone>: reads real secrets
        # from the HOST checkout, never copied into the clone (split-secrets, #595/#596).
        "secrets_env": {"CHAD_SECRETS_PATH": str(secrets_path)},
    }


def deprovision_eval_clone(path: str | Path, *, source_repo: Optional[Path] = None) -> Dict[str, Any]:
    """Remove a clone created by `provision_eval_clone`. Safe/idempotent — `git worktree remove`
    refuses (non-fatally, reported here) if `path` isn't a worktree of this repo."""
    source_repo = (source_repo or _repo_root()).resolve()
    dest = Path(path).resolve()
    rc, out, err = _run_git(["worktree", "remove", "--force", str(dest)], cwd=source_repo)
    if rc != 0 and dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
        return {"success": True, "note": f"git worktree remove failed ({err.strip() or out.strip()}); "
                f"deleted directory directly."}
    return {"success": True}
