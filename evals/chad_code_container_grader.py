"""Run the small fixbugs fixture in a disposable, restricted Docker container."""
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

# Pin the locally verified SDK image; never pull an image during grading.
GRADER_IMAGE = "sha256:83c3672080c79513fece63c6887dc5cc9200f017d6f4657a54e19cbeac4e931d"
GRADER_VERSION = "fixbugs-container-v1"
SCRIPT = '''import io,json,sys,unittest
sys.path.insert(0,"/grade")
stream=io.StringIO()
try:
    suite=unittest.defaultTestLoader.discover("/grade",pattern="test_mathlib.py")
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    outcome="verification_error" if result.errors or not result.testsRun else "tests_failed" if result.failures else "resolved"
    report=dict(outcome=outcome,tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),output=stream.getvalue())
except BaseException as exc:
    report=dict(outcome="verification_error",tests_run=0,failures=0,errors=1,output=repr(exc))
print("CHAD_GRADE_JSON:"+json.dumps(report),flush=True)
'''


def docker_executable():
    found = shutil.which("docker")
    windows = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    return found or (str(windows) if windows.is_file() else "docker")


def grade_container(source: bytes, tests: bytes, timeout_s: float) -> dict:
    docker = docker_executable()
    name = "chad-grade-" + uuid.uuid4().hex[:12]
    metadata = {"grader_version": GRADER_VERSION, "image": GRADER_IMAGE,
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "tests_sha256": hashlib.sha256(tests).hexdigest()}
    decode = lambda value: value.decode(errors="replace") if isinstance(value, bytes) else value or ""
    try:
        with tempfile.TemporaryDirectory(prefix="chad-grade-input-") as directory:
            root = Path(directory)
            (root / "mathlib.py").write_bytes(source)
            (root / "test_mathlib.py").write_bytes(tests)
            (root / "grade.py").write_text(SCRIPT, encoding="utf-8")
            args = [docker, "run", "--rm", "--pull=never", "--name", name,
                    "--network=none", "--read-only", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges", "--user=65534:65534",
                    "--memory=256m", "--cpus=1", "--pids-limit=32",
                    "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
                    "--mount", f"type=bind,source={root.resolve().as_posix()},target=/grade,readonly",
                    "--entrypoint=python", GRADER_IMAGE, "-I", "-B", "/grade/grade.py"]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
            lines = [line for line in proc.stdout.splitlines() if line.startswith("CHAD_GRADE_JSON:")]
            if proc.returncode != 0 or not lines:
                return dict(outcome="verification_error", detail="Grading container failed or returned no structured result",
                            returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, metadata=metadata)
            report = json.loads(lines[-1].split(":", 1)[1])
            if report.get("outcome") not in {"resolved", "tests_failed", "verification_error"}:
                raise ValueError("Invalid grader outcome")
            counts = {key: report[key] for key in ("tests_run", "failures", "errors")}
            if any(type(value) is not int or value < 0 for value in counts.values()):
                raise ValueError("Invalid test counts")
            expected = "verification_error" if counts["errors"] or not counts["tests_run"] else "tests_failed" if counts["failures"] else "resolved"
            if report["outcome"] != expected:
                raise ValueError("Grader counts disagree with outcome")
            metadata.update(counts)
            return dict(outcome=expected, detail="Independent container test run", returncode=proc.returncode,
                        stdout=proc.stdout, stderr=proc.stderr + report["output"], metadata=metadata)
    except subprocess.TimeoutExpired as exc:
        return dict(outcome="verification_error", detail="Grading container timed out", timed_out=True,
                    stdout=decode(exc.stdout), stderr=decode(exc.stderr), metadata=metadata)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(outcome="verification_error", detail=f"Could not grade in container: {exc}", metadata=metadata)
    finally:
        # Killing the Docker client does not reliably stop its container.
        try:
            subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
