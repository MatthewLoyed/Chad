"""Verification checkpoints for the opt-in local coding eval."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from chad_run_validation import IncompleteRunError

_GENERATED = {".git", "node_modules", "dist", "test-results", "playwright-report", "__pycache__"}
_CHECK_COMMAND = re.compile(r"\b(?:npm (?:run (?:build|lint|typecheck|test:e2e)|test)|npx (?:playwright|vitest))\b")


class LocalAcceptance:
    def __init__(self, workspace, verifier, timeout):
        self.workspace = Path(workspace)
        self.verifier = verifier
        self.timeout = timeout
        artifact_root = os.environ.get("CHAD_ACCEPTANCE_ARTIFACTS")
        self.artifacts = Path(tempfile.mkdtemp(prefix="chad-acceptance-", dir=artifact_root))
        self.results = {}

    def fingerprint(self):
        digest = hashlib.sha256()
        for directory, subdirs, files in self.workspace.walk():
            subdirs[:] = sorted(set(subdirs) - _GENERATED)
            for name in sorted(set(files) - _GENERATED):
                path = directory / name
                digest.update(str(path.relative_to(self.workspace)).encode() + b"\0")
                data = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
                digest.update(hashlib.sha256(data).digest())
        return digest.hexdigest()

    def should_checkpoint(self, event):
        observation = getattr(event, "observation", None)
        return (
            getattr(observation, "kind", None) == "TerminalObservation"
            and getattr(observation, "exit_code", None) == 0
            and _CHECK_COMMAND.search(getattr(observation, "command", None) or "")
            and self.fingerprint() not in self.results
        )

    def verify(self):
        fingerprint = self.fingerprint()
        if fingerprint in self.results:
            return self.results[fingerprint]

        log = self.artifacts / f"{fingerprint}.log"
        snapshot = self.artifacts / fingerprint
        shutil.copytree(self.workspace, snapshot, symlinks=True, ignore=shutil.ignore_patterns(*_GENERATED))
        # Dependencies are provisioned separately from the source snapshot.
        dependencies = self.workspace / "node_modules"
        if dependencies.is_dir():
            (snapshot / "node_modules").symlink_to(dependencies, target_is_directory=True)
        try:
            with log.open("wb") as output:
                result = subprocess.run(
                    [self.verifier], cwd=snapshot, stdout=output, stderr=subprocess.STDOUT,
                    timeout=self.timeout, check=False,
                )
        finally:
            shutil.rmtree(snapshot)
        if self.fingerprint() != fingerprint:
            raise IncompleteRunError("local_acceptance: workspace changed during verification")
        if result.returncode not in (0, 1):
            raise IncompleteRunError(f"local_acceptance: verifier error ({result.returncode}), log={log}")

        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        first_error = next((i for i, line in enumerate(lines)
                            if re.search(r"error|fail|assert", line, re.I)), None)
        excerpt = lines[first_error:first_error + 8] if first_error is not None else lines[-8:]
        summary = f"Verifier exit {result.returncode}. Full log: {log}\n" + "\n".join(excerpt)[:2000]
        self.results[fingerprint] = (result.returncode == 0, summary)
        (self.artifacts / "checkpoint.json").write_text(
            json.dumps({"fingerprint": fingerprint, "passed": result.returncode == 0, "feedback": summary}),
            encoding="utf-8",
        )
        print(f"CHAD_ACCEPTANCE_CHECKPOINT {summary}", flush=True)
        return self.results[fingerprint]
