# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the headless Chad Brain (src/remote/server.py) ->
Chad-Brain.exe.

One-folder, windowed off (console hidden — stdout/stderr are redirected to
log files by whatever launches it, same convention as
scripts/prod/chad_supervisor.ps1). This packages ONLY the Brain; it still
resolves config/, data/, profiles.yaml etc. via CHAD_HOME (see
src/shared/frozen_bootstrap.py and the chad_home.txt written by
build_exe.ps1) — same mechanism as chad_gui.spec, just for the server side.

Persona prompts (prompts/persona + config/persona, #589) resolve through the
same CHAD_HOME path via persona_store.baseline_dir(), so they are deliberately
NOT bundled here — bundling would freeze a copy that shadows the editable one.

No voice-mode extras (faster-whisper/kokoro/torch) are bundled: the headless
Brain has no audio (RemoteFactory uses MockAudio), so this stays lean.

Build with scripts/prod/build_exe.ps1 -Brain (or -All), or:
    pyinstaller packaging/chad_brain.spec --noconfirm
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is injected by PyInstaller = the directory holding this spec
# (packaging/). The repo root is one level up. Anchor everything to ROOT so the
# build works regardless of the current working directory.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

block_cipher = None

# --- Packages with dynamic imports / data files PyInstaller can't infer ---
# The server side's import graph: web framework, LLM adapters, DB/RAG clients,
# scheduling, and MCP/browser tooling. Deliberately excludes faster_whisper /
# kokoro / torch (voice-mode-only, GUI side).
_COLLECT = [
    "starlette",
    "uvicorn",
    "websockets",
    "litellm",          # LLM adapter (tokenizers/data, lazy provider imports)
    "tiktoken",
    "tiktoken_ext",
    "supabase",
    "postgrest",
    "langgraph",
    "langchain_openai",
    "mcp",
    "croniter",
    "playwright_stealth",
    "sentence_transformers",
]

datas = []
binaries = []
hiddenimports = []

for _pkg in _COLLECT:
    try:
        d, b, h = collect_all(_pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # Optional/absent package — skip rather than fail the whole build.
        pass

# litellm ships test/benchmark fixtures, a full bundled Next.js admin-
# dashboard static export (litellm/proxy/_experimental/out/, ~21MB/700+
# files), and per-provider proxy guardrail integrations (litellm/proxy/
# guardrails/guardrail_hooks/) — none of this is runtime code Chad uses.
# All three also contain deeply nested, long/encoded filenames that can
# exceed Windows' 260-char MAX_PATH once staged into the installer payload,
# making NSIS's File command fail outright (see chad_gui.spec for the exact
# failures). Drop them.
_EXCLUDE_DATA_MARKERS = (
    "guardrail_benchmarks",
    os.path.join("proxy", "_experimental"),
    os.path.join("proxy", "guardrails", "guardrail_hooks"),
)
datas = [d for d in datas if not any(m in d[0] for m in _EXCLUDE_DATA_MARKERS)]

# Our own source tree is imported dynamically in places (tool/skill scanners),
# so make sure the whole src package is bundled.
hiddenimports += collect_submodules("src")

a = Analysis(
    [os.path.join(ROOT, "src", "remote", "server.py")],
    pathex=[ROOT, os.path.join(ROOT, "scripts", "setup")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "test", "pytest", "faster_whisper", "kokoro"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Chad-Brain",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,             # headless — stdout/stderr redirected by the launcher
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "scripts", "prod", "chad.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Chad-Brain",
)
