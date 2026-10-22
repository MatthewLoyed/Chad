# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# -----------------------------------------------------------------------------
# RESOURCE COLLECTION
# -----------------------------------------------------------------------------
# Collect data from packages that often have issues
datas = []
binaries = []
hiddenimports = [
    'engineio.async_drivers.threading',
    'engineio.async_drivers.aiohttp',
    'dns.resolver',
    'scipy.special.cython_special',
    'sklearn.utils._cython_blas',
    'webview.platforms.winforms',  # For pywebview on Windows
    'win32timezone',               # Often missed by pywin32
]

# Add standard assets
# Format: (source_path, dest_folder)
datas += [
    ('src/assets', 'src/assets'),
    ('src/clients', 'src/clients'),
    ('src/core', 'src/core'),
    ('src/tools', 'src/tools'),
    ('src/shared', 'src/shared'),
    ('models', 'models'),
    ('config.yaml', '.'),
    ('preferences.yaml', '.'),
    ('profiles.yaml', '.'),
]

# Collect all from key libraries to be safe
# (pycaw, screeninfo, pyaudio often need binary helpers)
tmp_ret = collect_all('pycaw')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('screeninfo')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('rfc3987_syntax')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('pvporcupine')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('playwright_stealth')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


# -----------------------------------------------------------------------------
# ANALYSIS
# -----------------------------------------------------------------------------
a = Analysis(
    ['main_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# -----------------------------------------------------------------------------
# EXE (ONE FILE / ONE DIR)
# -----------------------------------------------------------------------------
# Using ONE DIR for faster startup and easier debugging of assets
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Chad',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True, # Set to False for production if you want to hide terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None, # Add icon path here if you have one, e.g. 'assets/icon.ico'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Chad',
)
