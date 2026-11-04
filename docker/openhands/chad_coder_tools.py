"""Command and exact replacement helpers callable through the coder terminal."""
import argparse
import difflib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import uuid


def run_command(arguments, artifacts, shell=False):
    directory = Path(artifacts)
    directory.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    log = directory / (run_id + ".log")
    command = ["bash", "-o", "pipefail", "-c", arguments[0]] if shell else arguments
    started = time.monotonic()
    metadata = {"command": command, "cwd": str(Path.cwd()), "log": str(log), "state": "running"}
    receipt = directory / (run_id + ".json")
    receipt.write_text(json.dumps(metadata), encoding="utf-8")
    with log.open("wb") as output:
        try:
            code = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=False).returncode
        except OSError as exc:
            code = 127 if isinstance(exc, FileNotFoundError) else 126
            output.write(f"{type(exc).__name__}: {exc}\n".encode("utf-8"))
    metadata.update(state="finished", exit_code=code,
                    duration_s=round(time.monotonic() - started, 3))
    receipt.write_text(json.dumps(metadata), encoding="utf-8")
    print(json.dumps(metadata))
    with log.open(encoding="utf-8", errors="replace") as output:
        for _ in range(8):
            line = output.readline()
            if not line:
                break
            print(line.rstrip())
    return code if code >= 0 else 128 - code


def replace_exact(path, old, new):
    target = Path(path)
    if target.is_symlink():
        raise ValueError("Edit the resolved regular file explicitly.")
    before = target.read_bytes().decode("utf-8")
    if not old or before.count(old) != 1:
        raise ValueError("Expected text must match exactly once; file was not changed.")
    after = before.replace(old, new, 1)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(after.encode("utf-8"))
        os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile=str(target), tofile=str(target)
    )))


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    run = commands.add_parser("run")
    run.add_argument("--shell", action="store_true")
    run.add_argument("command", nargs=argparse.REMAINDER)
    edit = commands.add_parser("replace")
    edit.add_argument("path")
    edit.add_argument("--old-file", required=True)
    edit.add_argument("--new-file", required=True)
    args = parser.parse_args()
    if args.operation == "replace":
        replace_exact(args.path, Path(args.old_file).read_bytes().decode("utf-8"),
                      Path(args.new_file).read_bytes().decode("utf-8"))
        return 0
    arguments = args.command
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    if not arguments or (args.shell and len(arguments) != 1):
        parser.error("Provide an argv command, or one quoted command string with --shell.")
    return run_command(arguments, os.environ.get("CHAD_RUN_ARTIFACTS", "/tmp/chad-tools"), args.shell)


if __name__ == "__main__":
    raise SystemExit(main())
