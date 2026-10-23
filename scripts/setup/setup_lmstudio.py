"""LM Studio cold-start automation (issue #406).

Ensures a fresh machine has LM Studio's local server running and the models
Chad needs by default already downloaded. Wraps LM Studio's `lms` CLI (ships
with LM Studio >= 0.3.x, added to PATH by the installer) so this can run
either as a standalone terminal step or be called from a future GUI
first-run wizard (see issue #218).

Deliberately narrow scope:
- Only handles LM Studio models. Embeddings (`nomic-ai/nomic-embed-text-v2-moe`)
  run locally via `sentence-transformers` (see requirements.txt) and are NOT
  fetched through LM Studio - nothing to do here for that model.
- Only offers the models Chad's default config actually needs to boot and
  hold a conversation. Advanced/optional models (code_agent's coder-14b,
  browser vision's vl-8b, the heavier 35B) are listed separately and never
  downloaded without --include-optional.
- Does not attempt anything for the froggeric chat-template fix - that only
  applies to the optional 35B model (see docs/features/LOCAL_MODELS.md).

Usage:
    python scripts/setup/setup_lmstudio.py            # interactive: check, then prompt before downloading
    python scripts/setup/setup_lmstudio.py --check    # report-only, no downloads/server start
    python scripts/setup/setup_lmstudio.py --yes      # non-interactive, download+start without prompting
    python scripts/setup/setup_lmstudio.py --json     # machine-readable summary on stdout (for a wizard)
    python scripts/setup/setup_lmstudio.py --include-optional --yes   # also grab advanced models
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

LMSTUDIO_BASE_URL = "http://localhost:1234/v1"

LMSTUDIO_DOWNLOAD_URL = "https://lmstudio.ai/download"


@dataclass
class ModelSpec:
    model_id: str
    role: str
    approx_vram_gb: int
    required: bool
    note: str = ""


# Mirrors config/config.yaml defaults. Keep this list in sync when the config
# defaults change — it is the source of truth the wizard/doc both read from.
REQUIRED_MODELS: list[ModelSpec] = [
    ModelSpec(
        model_id="google/gemma-4-26b-a4b-qat",
        role="main chat + judge",
        approx_vram_gb=16,
        required=True,
        note="local_models.active_model + reflection.model",
    ),
    ModelSpec(
        model_id="qwen/qwen3.5-9b",
        role="tool selector",
        approx_vram_gb=6,
        required=True,
        note="tools.llm_selector_model",
    ),
    ModelSpec(
        model_id="google/gemma-3-1b",
        role="intent router",
        approx_vram_gb=1,
        required=True,
        note="intent_router.model",
    ),
]

# Not offered by default — bigger, or only needed for specialist agents that
# are gated off in the public release anyway.
OPTIONAL_MODELS: list[ModelSpec] = [
    ModelSpec(
        model_id="qwen/qwen2.5-coder-14b",
        role="code_agent specialist",
        approx_vram_gb=10,
        required=False,
        note="only needed if you use the coding subagent",
    ),
    ModelSpec(
        model_id="qwen/qwen3-14b",
        role="general 14B alternative",
        approx_vram_gb=10,
        required=False,
    ),
    ModelSpec(
        model_id="qwen/qwen3.6-35b-a3b",
        role="heavier main-model option",
        approx_vram_gb=24,
        required=False,
        note="needs the froggeric chat template fix - see docs/features/LOCAL_MODELS.md",
    ),
    ModelSpec(
        model_id="qwen/qwen3-vl-8b",
        role="browser vision-assist",
        approx_vram_gb=6,
        required=False,
        note="browser_agent is gated off by default in this release",
    ),
]


@dataclass
class Report:
    lms_cli_found: bool = False
    server_running: bool = False
    server_started_by_us: bool = False
    models_present: list[str] = field(default_factory=list)
    models_missing: list[str] = field(default_factory=list)
    models_downloaded: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.lms_cli_found and self.server_running and not self.models_missing and not self.errors

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "lms_cli_found": self.lms_cli_found,
            "server_running": self.server_running,
            "server_started_by_us": self.server_started_by_us,
            "models_present": self.models_present,
            "models_missing": self.models_missing,
            "models_downloaded": self.models_downloaded,
            "errors": self.errors,
        }


def find_lms_cli() -> str | None:
    """Find the CLI even when Chad predates an installer PATH update."""
    on_path = shutil.which("lms")
    if on_path:
        return on_path
    filename = "lms.exe" if sys.platform == "win32" else "lms"
    for directory in (Path.home() / ".lmstudio" / "bin",
                      Path.home() / ".cache" / "lm-studio" / "bin"):
        candidate = directory / filename
        if candidate.is_file():
            return str(candidate)
    return None


def find_lmstudio_app() -> str | None:
    """Check common Windows install locations, independently of the CLI."""
    roots = [os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")]
    for root in filter(None, roots):
        for suffix in ("Programs/LM Studio/LM Studio.exe", "LM Studio/LM Studio.exe"):
            candidate = Path(root) / suffix
            if candidate.is_file():
                return str(candidate)
    return None


def is_server_running(base_url: str = LMSTUDIO_BASE_URL) -> bool:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/models", method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def list_downloaded_models(lms_path: str) -> list[str]:
    try:
        result = subprocess.run(
            [lms_path, "ls", "--json"], capture_output=True, text=True, timeout=15
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    # `lms ls --json` returns a list of model records; identifier key varies by
    # version, so check the common ones defensively.
    ids = []
    for entry in data if isinstance(data, list) else []:
        model_id = entry.get("modelKey") or entry.get("path") or entry.get("model") or entry.get("id")
        if model_id:
            ids.append(model_id)
    return ids


def start_server(lms_path: str) -> bool:
    try:
        subprocess.run([lms_path, "server", "start"], capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return False
    return is_server_running()


def download_model(lms_path: str, model_id: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [lms_path, "get", model_id, "--yes"],
            capture_output=True,
            text=True,
            timeout=1800,  # large GGUF downloads can take a while
        )
    except (subprocess.SubprocessError, OSError) as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "unknown lms error").strip()[-500:]
    return True, ""


def _is_already_present(model_id: str, downloaded: list[str]) -> bool:
    # `lms ls` identifiers don't always exactly match the catalog id used
    # elsewhere in config (quantization suffixes etc.) — substring match is
    # deliberately loose to avoid false "missing" reports.
    key = model_id.split("/")[-1].lower()
    return any(key in d.lower() for d in downloaded)


def run(models: list[ModelSpec], *, check_only: bool, assume_yes: bool, base_url: str = LMSTUDIO_BASE_URL) -> Report:
    report = Report()

    lms_path = find_lms_cli()
    report.lms_cli_found = lms_path is not None
    if not lms_path:
        report.errors.append(
            "`lms` CLI not found on PATH. Install/open LM Studio at least once "
            f"({LMSTUDIO_DOWNLOAD_URL}) - it ships `lms` and adds it to PATH. "
            "If it's still missing, run `lms bootstrap` from LM Studio's install directory."
        )
        return report

    downloaded = list_downloaded_models(lms_path)
    for spec in models:
        if _is_already_present(spec.model_id, downloaded):
            report.models_present.append(spec.model_id)
        else:
            report.models_missing.append(spec.model_id)

    report.server_running = is_server_running(base_url)

    if check_only:
        return report

    if report.models_missing and not assume_yes:
        print("\nThe following models are recommended to run Chad locally:")
        for spec in models:
            if spec.model_id in report.models_missing:
                print(f"  - {spec.model_id}  (~{spec.approx_vram_gb}GB, {spec.role})")
        answer = input("\nDownload these now via LM Studio's `lms` CLI? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("Skipping downloads.")
            return report

    still_missing = list(report.models_missing)
    report.models_missing = []
    for model_id in still_missing:
        print(f"Downloading {model_id} ...")
        ok, err = download_model(lms_path, model_id)
        if ok:
            report.models_downloaded.append(model_id)
        else:
            report.models_missing.append(model_id)
            report.errors.append(f"Failed to download {model_id}: {err}")

    if not report.server_running:
        if not assume_yes:
            answer = input("LM Studio server isn't running. Start it now? [Y/n] ").strip().lower()
            start = answer in ("", "y", "yes")
        else:
            start = True
        if start:
            report.server_started_by_us = start_server(lms_path)
            report.server_running = report.server_started_by_us
            if not report.server_started_by_us:
                report.errors.append("Could not start the LM Studio local server via `lms server start`.")

    return report


def print_summary(report: Report) -> None:
    print("\n--- LM Studio setup summary ---")
    print(f"lms CLI found:        {report.lms_cli_found}")
    print(f"Server running:       {report.server_running}")
    if report.models_present:
        print(f"Already downloaded:   {', '.join(report.models_present)}")
    if report.models_downloaded:
        print(f"Downloaded this run:  {', '.join(report.models_downloaded)}")
    if report.models_missing:
        print(f"Still missing:        {', '.join(report.models_missing)}")
    if report.errors:
        print("Errors:")
        for e in report.errors:
            print(f"  - {e}")
    print(f"Ready: {'yes' if report.ok else 'no'}")
    print(
        "\nNote: embeddings (nomic-embed-text-v2-moe) run locally via "
        "sentence-transformers - nothing to download in LM Studio for those."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report status only, no downloads/server start.")
    parser.add_argument("--yes", action="store_true", help="Don't prompt; download and start the server automatically.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON summary instead of text.")
    parser.add_argument(
        "--include-optional",
        action="store_true",
        help="Also offer the optional/advanced models (code_agent, vision-assist, 35B).",
    )
    args = parser.parse_args()

    models = list(REQUIRED_MODELS)
    if args.include_optional:
        models += OPTIONAL_MODELS

    report = run(models, check_only=args.check, assume_yes=args.yes)

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print_summary(report)

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
