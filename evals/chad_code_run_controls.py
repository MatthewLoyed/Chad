"""Trusted fixture controls and allowlisted run provenance for coding probes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from evals.chad_code_container_grader import docker_executable, grade_container

KNOWN_FIXED_MATHLIB = b'''def factorial(n):
    if n < 0: raise ValueError("negative")
    result = 1
    for i in range(1, n + 1): result *= i
    return result
def is_prime(n):
    return n >= 2 and all(n % i for i in range(2, n))
def divide(a, b):
    if b == 0: raise ValueError("zero")
    return a / b
def gcd(a, b):
    while b: a, b = b, a % b
    return abs(a)
def fib(n):
    if n < 0: raise ValueError("negative")
    a, b = 0, 1
    for _ in range(n): a, b = b, a + b
    return a
'''


def validate_controls(source: bytes, tests: bytes) -> dict:
    baseline = grade_container(source, tests, 30)
    oracle = grade_container(KNOWN_FIXED_MATHLIB, tests, 30)
    counts = baseline.get("metadata", {})
    # A fixture may fail by assertion or test error (e.g. wrong exception type).
    baseline_fails = (counts.get("tests_run", 0) > 0 and
                      counts.get("failures", 0) + counts.get("errors", 0) > 0 and
                      baseline["outcome"] != "resolved")
    return {"ready": bool(baseline_fails and oracle["outcome"] == "resolved"),
            "baseline": baseline, "known_fix": oracle}


def run_provenance(container: str, repo: Path, task: str) -> dict:
    report = {"task_sha256": hashlib.sha256(task.encode()).hexdigest()}
    git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    try:
        report["revision"] = subprocess.check_output([git, "rev-parse", "HEAD"], cwd=repo, text=True, timeout=10).strip()
        report["dirty"] = bool(subprocess.check_output([git, "status", "--porcelain"], cwd=repo, text=True, timeout=10).strip())
    except (OSError, subprocess.SubprocessError) as exc:
        report["revision_error"] = type(exc).__name__
    allowed = {"CHAD_MODEL", "CHAD_TEMPERATURE", "CHAD_MAX_ITER", "CHAD_TIMEOUT_S",
               "CHAD_LLM_TIMEOUT_S", "CHAD_NATIVE_TOOL_CALLING", "CHAD_USE_TASK_TRACKER",
               "CHAD_USE_SUBAGENTS", "CHAD_USE_CONDENSER", "CHAD_USE_CRITIC", "CHAD_SKILLS_DIR"}
    try:
        data = json.loads(subprocess.check_output([docker_executable(), "inspect", container], text=True, timeout=10))[0]
        report["worker_image"] = data["Image"]
        report["settings"] = {key: value for item in data["Config"].get("Env", [])
                              for key, _, value in [item.partition("=")] if key in allowed}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        report["worker_inspect_error"] = type(exc).__name__
    return report
