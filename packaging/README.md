# Packaging — Chad desktop app (`Chad.exe` + `Chad-Brain.exe`)

This folder packages the **GUI (Limb)** and the **headless Brain** into real,
installable Windows apps. Both still read `config/`, `data/`, `profiles.yaml`,
etc. from this repo checkout via **`CHAD_HOME`** — freezing doesn't change
what backs the app, it just makes it runnable without a Python/Node install.

If a Brain is already running (e.g. Matthew's always-on prod supervisor on
port 8767), the GUI just connects to it as before. If none is reachable, the
GUI **auto-starts a local Brain** (`Chad-Brain.exe` next to it when frozen,
or `python src/remote/server.py` from a source checkout) — see
`src/remote/limb.py: _try_autostart_brain`. This only ever targets `localhost`
URLs and only fires once per ~45s cooldown, so it never fights an existing
Brain (local or prod).

## What gets built

`scripts/prod/build_exe.ps1 -Gui` (or `-Brain`, or `-All`) produces one or
both **one-folder** PyInstaller builds:

```
dist/Chad/
  Chad.exe          # windowed (no console flash), chad.ico embedded
  chad_home.txt     # absolute path to this repo (CHAD_HOME)
  _internal/        # frozen Python + deps + bundled web UI (web/dist/)

dist/Chad-Brain/
  Chad-Brain.exe    # headless (no window), chad.ico embedded
  chad_home.txt     # absolute path to this repo (CHAD_HOME)
  _internal/        # frozen Python + server-side deps (no voice-mode extras)
```

- The React UI is built (`npm run build`) and bundled inside the GUI app, so
  it renders without the source tree's `dist/`.
- `config/`, `data/`, `profiles.yaml`, voice models, etc. still live in the
  repo. Both frozen apps find them via **`CHAD_HOME`**.
- The Brain build excludes voice-mode extras (faster-whisper/kokoro/torch) —
  the headless Brain has no audio (`RemoteFactory` uses `MockAudio`), so it
  stays leaner than the GUI's full dependency set.

### How `CHAD_HOME` works

A frozen `Chad.exe` can't use `__file__`/cwd to find the repo (those point
inside the bundle). On startup `src/shared/frozen_bootstrap.py`:

1. reads `CHAD_HOME` (env var → `chad_home.txt` next to the exe → exe dir),
2. exports it and `chdir`s into it.

`get_project_root()` (`src/shared/paths.py`) and `.env` loading
(`src/shared/config_manager.py`) honor `CHAD_HOME` first, so every repo-relative
path resolves to the real checkout. From a normal source run, none of this
triggers (`sys.frozen` is unset) — it's a no-op.

## Build it

```powershell
# GUI only (default when no switch is passed), from the repo root:
powershell -ExecutionPolicy Bypass -File scripts\prod\build_exe.ps1

# Brain only:
powershell -ExecutionPolicy Bypass -File scripts\prod\build_exe.ps1 -Brain

# Both:
powershell -ExecutionPolicy Bypass -File scripts\prod\build_exe.ps1 -All

# From a git worktree (the .venv lives in the main checkout):
powershell -ExecutionPolicy Bypass -File scripts\prod\build_exe.ps1 -All `
    -Python C:\path\to\main\.venv\Scripts\python.exe

# Reuse an existing web build (GUI only):
... build_exe.ps1 -Gui -SkipWeb
```

Test it by double-clicking `dist\Chad\Chad.exe`. If no Brain is already
running, it spawns `dist\Chad-Brain\Chad-Brain.exe` automatically and waits
for it to come up before connecting.

## Install it as an app

`scripts/prod/install_prod.ps1` automatically **prefers `dist\Chad\Chad.exe`**
when present (otherwise it falls back to the windowless VBS launcher). It then:

- creates the **Desktop** and **Start-menu** "Chad" shortcuts pointing at the exe,
- registers an **Add/Remove Programs** entry (Settings → Apps → Chad) whose
  uninstall runs `uninstall_prod.ps1`.

`uninstall_prod.ps1` removes the shortcuts, the Start-menu folder, and the
Add/Remove Programs key (and stops the Brain + autostart, as before).

## Single-file installer (`Chad-Setup-x.x.x.exe`, issue #429)

`build_exe.ps1` + `install_prod.ps1` above assume a full source checkout
already exists on the machine — fine for the maintainer's own dev box, not
for a stranger downloading one file. `build_installer.ps1` closes that gap:

Installer staging also places the C++ runtime already redistributed by the
sklearn wheel at the Brain's DLL search root. A clean Windows machine must
load PyTorch's `c10.dll` without relying on a separately installed Visual C++
runtime; otherwise local embeddings fall back to keyword search. Missing
bundled runtime files fail staging instead of silently producing that installer.

```powershell
# NSIS must be installed once: winget install NSIS.NSIS
powershell -ExecutionPolicy Bypass -File scripts\prod\build_installer.ps1
# or pin a version explicitly:
... build_installer.ps1 -Version 1.0.0
```

This runs, in order:

1. `build_exe.ps1 -All` — produces `dist\Chad\` and `dist\Chad-Brain\` as usual.
2. `stage_installer_payload.ps1` — copies both into `dist\installer_payload\`
   alongside the **tracked, non-secret config surface** (`config/*.yaml`,
   `*.py`, `*.json`, `*.jinja`, the `*.example` templates, root `.env.example`
   / `CLAUDE.md.example`) and strips the build-machine `chad_home.txt` from
   each app folder — this payload is fully self-contained, unlike a raw
   `dist\Chad\` copy.
   It also includes the tracked baseline `prompts/`, tracked `src/skills/`
   instructions/scripts, tracked `src/tools/` for filesystem discovery, and
   `LICENSE`. Personal overlays under `config/persona/`
   and untracked skill files are excluded.
3. `makensis packaging\chad_installer.nsi` — wraps the payload into
   `dist\Chad-Setup-<version>.exe`: installs to `%LOCALAPPDATA%\Chad\`
   (per-user, no admin), writes a fresh `chad_home.txt` in each app folder
   pointing at the real install dir, creates Desktop/Start-menu shortcuts and
   an Add/Remove Programs entry, and offers an opt-in component to start the
   Brain automatically at login. Branded with `scripts\prod\chad.ico`
   (stock MUI2 wizard chrome otherwise — a fully custom banner/wizard image is
   a follow-up, not yet done).

Uninstalling removes the app binaries, shortcuts, and registry entries but
**deliberately leaves `config\` (may hold a real API key) and `data\`
(local chat history/memory) behind** — delete the install folder manually for
a fully clean removal.

## Notes / follow-ups

- **WebView2 runtime** is assumed present (it ships with current Windows 11 and
  is already required by the script-launched GUI).
- **Code signing** is scaffolded but inactive: `build_installer.ps1` calls
  `scripts\prod\sign_installer.ps1`, which no-ops (leaves the exe unsigned)
  unless `CHAD_SIGN_PFX_BASE64` / `CHAD_SIGN_PFX_PASSWORD` are set (locally
  or as GitHub Actions secrets for `.github/workflows/release.yml`). No cert
  has been purchased yet — see #430 — so installers stay unsigned and
  SmartScreen may warn on first run.
- **Automated releases**: pushing a `vX.Y.Z` tag runs
  `.github/workflows/release.yml`, which builds the installer and attaches
  it to the GitHub Release automatically — see `docs/RELEASING.md`.
- **Auto-update**: the Brain self-updates via Settings → System → Update. The
  packaged GUI is rebuilt by re-running `build_exe.ps1`; a binary auto-update
  channel is out of scope for now (see issue #180).
- This is a per-user install (HKCU + per-user Startup/Start-menu) — **no admin
  required**.
- The installer's own copy of `config\secrets.yaml` / `preferences.yaml`
  doesn't exist until first launch — the first-run setup wizard (#428) /
  `bootstrap_config_if_needed()` instantiate them from the bundled `.example`
  templates and collect an LLM key.

The frozen GUI starts its OS helper with `Chad.exe --hands-daemon`. The entrypoint dispatches that flag before GUI imports and the GUI singleton lock; passing a Python script path to the frozen executable would recursively launch another GUI and can crash WebView on reopen.

On reopen, the status bridge exposes the cached state enum so React can recover connection updates delivered before the page was ready. The OS helper reads the profile token at handshake time, including credentials generated by the Brain on first launch.

When a test is running from an earlier payload, build with `-PayloadName installer_payload_<build-id>` to stage separately. Staging names are restricted to a child directory under `dist`; never restage over a running candidate.
