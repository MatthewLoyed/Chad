"""E2B sandbox tools — code_agent's actual "run code -> get result" round trip.

Highest-yield gap identified for issue #216: src/tools/coding/sandbox.py had zero automated
test coverage (only a live, assertion-free manual script hitting the real E2B API). These
tests fake the e2b_code_interpreter.Sandbox surface so the round trip — execute, snapshot new
files, auto-deliver artifacts, self-heal a misspelled download path, and recover from a dead
cached sandbox — is provable without live E2B credentials.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.tools.coding.sandbox as sandbox_mod
from src.tools.coding.sandbox import (
    DownloadSandboxFileTool,
    ExecuteBashTool,
    ExecutePythonTool,
    InstallPackageTool,
    get_sandbox,
)


class _FakeFileEntry:
    def __init__(self, name, is_dir=False):
        self.name = name
        self.is_dir = is_dir


class _FakeFiles:
    def __init__(self, listing=None, contents=None):
        self._listing = listing or {}
        self._contents = contents or {}

    def list(self, path):
        return self._listing.get(path, [])

    def read(self, path, format="bytes"):
        if path not in self._contents:
            raise FileNotFoundError(path)
        return self._contents[path]

    def add_file(self, path, dirpath, name, content=b"data"):
        self._listing.setdefault(dirpath, []).append(_FakeFileEntry(name))
        self._contents[path] = content


class _FakeCommandResult:
    def __init__(self, stdout="", stderr="", exit_code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _FakeCommands:
    def __init__(self, result):
        self.result = result
        self.last_command = None

    def run(self, command, timeout=None):
        self.last_command = command
        return self.result


class _FakeExecution:
    def __init__(self, stdout="", stderr="", results=None, error=None):
        self.logs = SimpleNamespace(stdout=stdout, stderr=stderr)
        self.results = results or []
        self.error = error


class _FakeSandbox:
    def __init__(self, running=True):
        self.files = _FakeFiles()
        self.commands = _FakeCommands(_FakeCommandResult())
        self._running = running
        self.timeout_calls = []
        self._run_code_result = _FakeExecution(stdout="ok")
        self._run_code_raises = None

    def is_running(self):
        return self._running

    def set_timeout(self, t):
        self.timeout_calls.append(t)

    def run_code(self, code, timeout=None):
        if self._run_code_raises:
            raise self._run_code_raises
        return self._run_code_result


@pytest.fixture(autouse=True)
def _reset_singleton():
    sandbox_mod._sandbox_instance = None
    yield
    sandbox_mod._sandbox_instance = None


# --- get_sandbox(): cache reuse / self-healing recreation ------------------ #

def test_get_sandbox_reuses_running_instance_and_extends_ttl():
    fake = _FakeSandbox(running=True)
    sandbox_mod._sandbox_instance = fake

    result = get_sandbox()

    assert result is fake
    assert fake.timeout_calls, "reuse must extend the TTL (keepalive)"


def test_get_sandbox_recreates_when_cached_instance_is_dead():
    dead = _FakeSandbox(running=False)
    sandbox_mod._sandbox_instance = dead
    fresh = _FakeSandbox(running=True)

    with patch.object(sandbox_mod, "_new_sandbox", return_value=fresh) as new_fn:
        result = get_sandbox()

    new_fn.assert_called_once()
    assert result is fresh


def test_get_sandbox_recreates_when_is_running_raises():
    class _Flaky(_FakeSandbox):
        def is_running(self):
            raise ConnectionError("sandbox unreachable")

    sandbox_mod._sandbox_instance = _Flaky()
    fresh = _FakeSandbox(running=True)

    with patch.object(sandbox_mod, "_new_sandbox", return_value=fresh):
        result = get_sandbox()

    assert result is fresh


# --- ExecutePythonTool: full round trip ------------------------------------ #

@pytest.mark.asyncio
async def test_execute_python_returns_stdout_and_result():
    fake = _FakeSandbox()
    fake._run_code_result = _FakeExecution(stdout="42\n", results=["42"])

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake):
        result = await ExecutePythonTool().execute(code="print(6*7)")

    assert result["success"]
    assert result["stdout"] == "42\n"
    assert result["results"] == ["42"]
    assert result["error"] is None


@pytest.mark.asyncio
async def test_execute_python_auto_delivers_new_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = _FakeSandbox()
    fake._run_code_result = _FakeExecution(stdout="done")
    # Simulate the file appearing as a side effect of run_code (not present in the
    # "before" snapshot), the same way a real script writes a file mid-execution.
    fake.run_code = lambda code, timeout=None: (
        fake.files.add_file("/home/user/report.docx", "/home/user", "report.docx", content=b"docx-bytes"),
        fake._run_code_result,
    )[1]

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake), \
         patch.object(sandbox_mod, "register_artifact_bytes", return_value="tok123") as reg:
        result = await ExecutePythonTool().execute(code="make_report()")

    assert result["success"]
    assert result["auto_delivered_artifacts"] == [
        {"filename": "report.docx", "local_path": str(tmp_path / "artifacts" / "report.docx"), "token": "tok123"}
    ]
    assert "Auto-delivered generated artifact" in result["stdout"]
    reg.assert_called_once()
    assert (tmp_path / "artifacts" / "report.docx").read_bytes() == b"docx-bytes"


@pytest.mark.asyncio
async def test_execute_python_sandbox_exception_returns_failure_not_a_crash():
    fake = _FakeSandbox()
    fake._run_code_raises = RuntimeError("sandbox died mid-execution")

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake):
        result = await ExecutePythonTool().execute(code="whatever")

    assert result["success"] is False
    assert "sandbox died mid-execution" in result["error"]


# --- ExecuteBashTool --------------------------------------------------------- #

@pytest.mark.asyncio
async def test_execute_bash_returns_stdout_stderr_exit_code():
    fake = _FakeSandbox()
    fake.commands = _FakeCommands(_FakeCommandResult(stdout="hi", stderr="", exit_code=0))

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake):
        result = await ExecuteBashTool().execute(command="echo hi")

    assert result["success"]
    assert result["stdout"] == "hi"
    assert result["exit_code"] == 0
    assert fake.commands.last_command == "echo hi"


@pytest.mark.asyncio
async def test_execute_bash_nonzero_exit_still_reports_success_true_with_exit_code():
    """The tool call itself succeeded (bash ran); a failing command is reported via
    exit_code, not by making the whole tool call fail — callers must check exit_code."""
    fake = _FakeSandbox()
    fake.commands = _FakeCommands(_FakeCommandResult(stdout="", stderr="not found", exit_code=127))

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake):
        result = await ExecuteBashTool().execute(command="nonexistent_cmd")

    assert result["success"] is True
    assert result["exit_code"] == 127
    assert result["stderr"] == "not found"


# --- InstallPackageTool ------------------------------------------------------ #

@pytest.mark.asyncio
async def test_install_package_success():
    fake = _FakeSandbox()
    fake._run_code_result = _FakeExecution(stdout="Successfully installed pandas")

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake):
        result = await InstallPackageTool().execute(package="pandas")

    assert result["success"]
    assert "pandas" in result["result"]


@pytest.mark.asyncio
async def test_install_package_sandbox_failure_returns_error():
    fake = _FakeSandbox()
    fake._run_code_raises = RuntimeError("no network in sandbox")

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake):
        result = await InstallPackageTool().execute(package="pandas")

    assert result["success"] is False
    assert "no network" in result["error"]


# --- DownloadSandboxFileTool: exact path + self-healing --------------------- #

@pytest.mark.asyncio
async def test_download_exact_path_succeeds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = _FakeSandbox()
    fake.files.add_file("/home/user/output.docx", "/home/user", "output.docx", content=b"real-bytes")

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake), \
         patch.object(sandbox_mod, "register_artifact_bytes", return_value="tok456"):
        result = await DownloadSandboxFileTool().execute(
            sandbox_path="/home/user/output.docx", local_filename="output.docx"
        )

    assert result["success"]
    assert result["filename"] == "output.docx"
    assert "Self-healed" not in result["result"]


@pytest.mark.asyncio
async def test_download_self_heals_case_mismatch(tmp_path, monkeypatch):
    """User/model asked for 'Output.DOCX' but the sandbox actually has 'output.docx'."""
    monkeypatch.chdir(tmp_path)
    fake = _FakeSandbox()
    fake.files.add_file("/home/user/output.docx", "/home/user", "output.docx", content=b"real-bytes")

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake), \
         patch.object(sandbox_mod, "register_artifact_bytes", return_value="tok789"):
        result = await DownloadSandboxFileTool().execute(
            sandbox_path="/home/user/Output.DOCX", local_filename="Output.DOCX"
        )

    assert result["success"]
    assert result["filename"] == "output.docx"
    assert "Self-healed" in result["result"]


@pytest.mark.asyncio
async def test_download_self_heals_single_matching_extension(tmp_path, monkeypatch):
    """Wrong basename entirely, but only one .pdf exists in the target dir — resolve to it."""
    monkeypatch.chdir(tmp_path)
    fake = _FakeSandbox()
    fake.files.add_file("/home/user/final_report_v2.pdf", "/home/user", "final_report_v2.pdf", content=b"pdf-bytes")

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake), \
         patch.object(sandbox_mod, "register_artifact_bytes", return_value="tokabc"):
        result = await DownloadSandboxFileTool().execute(
            sandbox_path="/home/user/report.pdf", local_filename="report.pdf"
        )

    assert result["success"]
    assert result["filename"] == "final_report_v2.pdf"


@pytest.mark.asyncio
async def test_download_missing_file_and_no_candidates_returns_error():
    fake = _FakeSandbox()  # empty files entirely

    with patch.object(sandbox_mod, "get_sandbox", return_value=fake):
        result = await DownloadSandboxFileTool().execute(
            sandbox_path="/home/user/ghost.pdf", local_filename="ghost.pdf"
        )

    assert result["success"] is False
    assert "not found" in result["error"]


def test_resolve_e2b_api_key_strips_quotes_and_bearer(monkeypatch):
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.setattr(
        sandbox_mod.ConfigManager,
        "get_secret",
        lambda key, default=None: '  "e2b_test_key_abc"  ',
    )
    assert sandbox_mod.resolve_e2b_api_key() == "e2b_test_key_abc"

    monkeypatch.setattr(
        sandbox_mod.ConfigManager,
        "get_secret",
        lambda key, default=None: "Bearer e2b_test_key_xyz",
    )
    assert sandbox_mod.resolve_e2b_api_key() == "e2b_test_key_xyz"


def test_resolve_e2b_api_key_uses_secrets_not_stale_env(monkeypatch):
    """Settings/secrets.yaml wins over a stale .env E2B key (both look valid)."""
    monkeypatch.setenv("E2B_API_KEY", "e2b_from_env_stale_keyxxxxxxx")
    monkeypatch.setattr(
        sandbox_mod.ConfigManager,
        "get_secret",
        lambda key, default=None: "e2b_from_secrets_good_keyxxxx",
    )
    assert sandbox_mod.resolve_e2b_api_key() == "e2b_from_secrets_good_keyxxxx"


def test_classify_sandbox_error_marks_401_non_retryable():
    """401 resolves to the shared `auth` class (was the sandbox-local `sandbox_auth`)."""
    payload = sandbox_mod.sandbox_failure_response(
        "401: Unauthorized, please check your credentials. - authorization header is malformed"
    )
    assert payload["failure_class"] == "auth"
    assert payload["retryable"] is False


def test_sandbox_expired_stays_retryable():
    """A 502 from E2B means the sandbox died — retry recreates it. Domain matcher
    must win over the generic 5xx→unavailable rule."""
    payload = sandbox_mod.sandbox_failure_response("502: The sandbox was not found")
    assert payload["failure_class"] == "expired"
    assert payload["retryable"] is True


@pytest.mark.asyncio
async def test_execute_bash_auth_error_is_structured():
    with patch.object(
        sandbox_mod,
        "get_sandbox",
        side_effect=Exception(
            "401: Unauthorized, please check your credentials. - authorization header is malformed"
        ),
    ):
        result = await ExecuteBashTool().execute(command="date")

    assert result["success"] is False
    assert result["retryable"] is False
    assert result["failure_class"] == "auth"
    # The rendered text must carry the domain-specific recovery move, not just the class.
    assert "E2B_API_KEY" in result["error"]
    assert "NEXT_ACTIONS" in result["error"]
