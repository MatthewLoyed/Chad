# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Chad desktop GUI (the Limb) -> Chad.exe.

One-folder, windowed (no console), embedded icon. This packages ONLY the GUI
client; the always-on Brain keeps running from the source checkout under the
supervisor. The frozen app locates that checkout via CHAD_HOME (see
src/shared/frozen_bootstrap.py and the chad_home.txt written by build_exe.ps1).

Build with scripts/prod/build_exe.ps1 (which first builds the web UI), or:
    pyinstaller packaging/chad_gui.spec --noconfirm
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is injected by PyInstaller = the directory holding this spec
# (packaging/). The repo root is one level up. Anchor everything to ROOT so the
# build works regardless of the current working directory.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

block_cipher = None

# --- Packages with dynamic imports / data files PyInstaller can't infer ---
# collect_all pulls submodules + data + binaries for each. Voice mode
# (faster-whisper/kokoro) is deliberately NOT bundled here — it's out of
# scope for the initial installer release (issue #218/#429) and pulling
# them in drags torch/tensorflow/onnxruntime into the build, ~doubling
# PyInstaller analysis time. Re-add them once voice mode ships in the
# installed app; source checkouts still get voice via requirements-voice.txt.
_COLLECT = [
    "webview",          # pywebview WebView2 host (.NET / clr glue + JS shims)
    "comtypes",         # COM bindings used by pycaw (volume) — runtime codegen
    "pycaw",            # Windows audio session control
    "litellm",          # LLM adapter (tokenizers/data, lazy provider imports)
    "tiktoken",
    "tiktoken_ext",
    "supabase",
    "postgrest",
    "websockets",
    "huggingface_hub",  # explicit optional embedding download, no inference stack
    "tqdm",
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
# guardrails/guardrail_hooks/ — aim, aporia, azure, bedrock, etc., plus
# multi-language policy_templates/ under litellm_content_filter/) — none of
# this is runtime code Chad uses (Chad only calls litellm as a completion
# adapter; it never runs litellm's proxy server, so no guardrail hook is
# ever invoked). All three also contain deeply nested, long/encoded
# filenames that can exceed Windows' 260-char MAX_PATH once staged into the
# installer payload, making NSIS's File command fail outright — the
# guardrail_hooks policy templates hit exactly that limit even from a
# short-ish build path. Drop them.
_EXCLUDE_DATA_MARKERS = (
    "guardrail_benchmarks",
    os.path.join("proxy", "_experimental"),
    os.path.join("proxy", "guardrails", "guardrail_hooks"),
)
datas = [d for d in datas if not any(m in d[0] for m in _EXCLUDE_DATA_MARKERS)]

# Our own source tree is imported dynamically in places (tool/skill scanners),
# so make sure the whole src package is bundled.
hiddenimports += collect_submodules("src")

# Bundle the production React build inside the app (loaded when frozen).
datas += [(os.path.join(ROOT, "src", "clients", "web", "dist"), "web/dist")]
datas += [(os.path.join(ROOT, "config", "hardware_models.json"), "config")]

a = Analysis(
    [os.path.join(ROOT, "main_gui.py")],
    pathex=[ROOT, os.path.join(ROOT, "scripts", "setup")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The GUI (Limb) is a pure display/uplink client — tool RAG, memory search,
    # and embeddings all run Brain-side (see LightweightRegistryStub in
    # src/clients/desktop/app_controller.py). collect_submodules("src") below
    # still statically scans those Brain-only modules (dynamic tool/skill
    # scanners need the whole src package), which drags in the local-RAG ML
    # stack (torch/sklearn/scipy/pandas/transformers, ~1GB) even though the
    # GUI process never actually imports it at runtime. Exclude it here;
    # chad_brain.spec bundles it for real.
    excludes=[
        "tkinter.test", "test", "pytest", "faster_whisper", "kokoro",
        "torch", "sentence_transformers", "transformers", "sklearn",
        "tensorflow", "onnxruntime", "scipy", "pandas",
    ],
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
    name="Chad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # windowed app — no console flash
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
    name="Chad",
)
