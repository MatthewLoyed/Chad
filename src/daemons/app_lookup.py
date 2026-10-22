"""Read-only launch-target resolution shared by app lookup and app launch."""
from __future__ import annotations
import os
import shutil
from typing import Optional


def resolve_application(name: str, explicit_path: Optional[str] = None) -> dict:
    """Resolve config, PATH, known aliases, Start Menu or a direct path; never spawn."""
    app_name = name.lower().strip()
    if not app_name:
        raise ValueError("Application name must not be empty.")
    target_path = None
    method = "unknown"

    # 1. If the server sent us a validated configuration path, trust it
    if explicit_path and os.path.exists(explicit_path):
        target_path = explicit_path
        method = "config"
    else:
        # 2. Check System PATH
        path_match = shutil.which(app_name) or shutil.which(app_name + ".exe")
        if path_match:
            target_path = path_match
            method = "path"
        else:
            # 3. Virtual App URI Handlers
            virtual_apps = {
                "settings": "ms-settings:",
                "calculator": "ms-calculator:",
                "photos": "ms-photos:",
                "display": "ms-settings:display",
                "monitors": "ms-settings:display",
                "gmail": "https://mail.google.com"
            }
            if app_name in virtual_apps:
                target_path = virtual_apps[app_name]
                method = "url" if target_path.startswith("http") else "windows_uri"
            else:
                # 4. Deep Start Menu Scan
                paths = [
                    os.path.join(os.environ.get("ProgramData", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
                    os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")
                ]
                best_match = None
                best_score = 0
                for base_path in paths:
                    if not os.path.exists(base_path): continue
                    for root, _, files in os.walk(base_path):
                        for file in files:
                            if file.lower().endswith(".lnk"):
                                shortcut_name = os.path.splitext(file)[0].lower()
                                score = 100 if shortcut_name == app_name else 80 if shortcut_name.startswith(app_name) else 50 if app_name in shortcut_name else 0
                                if score > best_score:
                                    best_score = score
                                    best_match = os.path.join(root, file)
                                if score == 100: break
                    if best_score == 100: break

                if best_match and best_score >= 30:
                    target_path = best_match
                    method = "start_menu"

    # 5. Absolute Direct Path Fallback
    if not target_path:
        raw_name = name
        if os.path.isabs(raw_name) and os.path.exists(raw_name):
            target_path = raw_name
            method = "direct"
        else:
            raise RuntimeError(f"Application '{app_name}' could not be resolved on local filesystem.")

    return {"path": target_path, "method": method}


def check_application(name: str, explicit_path: Optional[str] = None) -> dict:
    """Inspect a resolved file/shortcut without testing launch or changing windows."""
    try:
        match = resolve_application(name, explicit_path)
    except RuntimeError:
        return {"name": name, "found": False, "launch_tested": False,
                "note": "No match in configured paths, PATH, Start Menu or supplied absolute path. Portable apps elsewhere may still exist."}
    path, method = match["path"], match["method"]
    result = {"name": name, "path": path, "method": method, "launch_tested": False}
    if method in {"url", "windows_uri"}:
        return {**result, "found": False, "executable_exists": None,
                "note": "Known web/URI alias only; this does not verify a locally installed application."}
    if not os.path.isfile(path):
        return {**result, "found": False, "executable_exists": False, "note": "Resolved path is not a file."}
    if not path.lower().endswith(".lnk"):
        return {**result, "found": True, "executable_exists": True,
                "note": "Launch target file exists; successful startup was not tested."}
    target = _read_shortcut_target(path)
    if target:
        exists = os.path.isfile(target)
        return {**result, "found": exists, "resolved_target": target, "executable_exists": exists,
                "note": "Shortcut target file exists; startup was not tested." if exists else "Shortcut target file is missing."}
    return {**result, "found": True, "executable_exists": None,
            "note": "Start Menu shortcut exists, but its target could not be verified; installation and startup remain unverified."}


def _read_shortcut_target(path: str) -> Optional[str]:
    # Read an existing shortcut without invoking it or saving changes.
    initialized = False
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        initialized = True
        target = win32com.client.Dispatch("WScript.Shell").CreateShortcut(path).TargetPath
        return os.path.expandvars(target) if target else None
    except Exception:
        return None
    finally:
        if initialized:
            pythoncom.CoUninitialize()
