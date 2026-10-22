"""Independent, standard-library outcome checks for the fixbugs coding preset."""
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import sys
import tempfile


@dataclass
class VerificationResult:
    outcome: str
    detail: str = ""
    returncode: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    metadata: dict = field(default_factory=dict)


def capture_fixbugs_fixture(workspace: Path, authoritative_tests: bytes) -> tuple[bytes, bytes]:
    """Validate a caller's fixture before spending a model turn; never overwrite it."""
    source = workspace / "mathlib.py"
    tests = workspace / "test_mathlib.py"
    if not source.is_file() or not tests.is_file():
        raise ValueError("fixbugs requires mathlib.py and test_mathlib.py")
    protected_tests = tests.read_bytes()
    if protected_tests.replace(b"\r\n", b"\n") != authoritative_tests.replace(b"\r\n", b"\n"):
        raise ValueError("fixbugs tests differ from the authoritative preset")
    return source.read_bytes(), protected_tests


def verify_fixbugs(workspace: Path, baseline_source: bytes, authoritative_tests: bytes,
                   status: str, exit_code: str | int | None, timeout_s: float = 30,
                   *, container: bool = False) -> VerificationResult:
    if status == "running":
        return VerificationResult("timeout", "Worker still running at poll deadline", timed_out=True)
    if status != "finished" or str(exit_code) != "0":
        return VerificationResult("backend_error", f"Worker status={status}, exit_code={exit_code}")
    try:
        source = (workspace / "mathlib.py").read_bytes()
        tests = (workspace / "test_mathlib.py").read_bytes()
    except OSError as exc:
        return VerificationResult("verification_error", f"Missing/unreadable fixture: {exc}")
    if tests != authoritative_tests:
        return VerificationResult("tests_modified", "Worker changed the protected test suite")
    if source == baseline_source:
        return VerificationResult("no_op", "Source is unchanged")

    if container:
        from evals.chad_code_container_grader import grade_container
        return VerificationResult(**grade_container(source, authoritative_tests, timeout_s))

    # Import unittest before exposing the generated source directory. Use a fresh
    # process and directory, so worker-edited tests or import hooks cannot grade it.
    script = (
        "import sys,unittest; sys.path.insert(0,sys.argv[1]); "
        "suite=unittest.defaultTestLoader.discover(sys.argv[1],pattern='test_mathlib.py'); "
        "result=unittest.TextTestRunner(verbosity=2).run(suite); "
        "sys.exit(2 if result.errors or result.testsRun == 0 else 0 if result.wasSuccessful() else 1)"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="chad-fixbugs-grade-") as directory:
            grade = Path(directory)
            (grade / "mathlib.py").write_bytes(source)
            (grade / "test_mathlib.py").write_bytes(authoritative_tests)
            result = subprocess.run([sys.executable, "-I", "-c", script, str(grade)],
                                    cwd=grade, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        decode = lambda value: value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        return VerificationResult("verification_error", "Verification subprocess timed out", timed_out=True,
                                  stdout=decode(exc.stdout), stderr=decode(exc.stderr))
    except OSError as exc:
        return VerificationResult("verification_error", f"Could not execute verifier: {exc}")
    outcome = "resolved" if result.returncode == 0 else "tests_failed" if result.returncode == 1 else "verification_error"
    return VerificationResult(outcome, "Independent authoritative test run", result.returncode,
                              stdout=result.stdout, stderr=result.stderr)


def verification_exit_code(status: str, verification: VerificationResult | None) -> int:
    """fixbugs requires verified correctness; other presets retain completion semantics."""
    if verification is not None:
        return 0 if status == "finished" and verification.outcome == "resolved" else 2
    return 0 if status == "finished" else 2
