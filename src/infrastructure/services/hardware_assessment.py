"""Read-only local hardware inventory. Unknown capacity is never reported as zero.

This scans the process host, not an arbitrary model endpoint. No hardware data
leaves the machine, and no model/driver is installed or loaded here.
"""
from __future__ import annotations

import csv
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

from src.core.tools.limits import get as limit

GIB = 1024 ** 3


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=limit("hardware_probe_timeout"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _nvidia() -> list[dict]:
    output = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                   "--format=csv,noheader,nounits"])
    devices = []
    for row in csv.reader(output.splitlines()):
        if len(row) != 3:
            continue
        try:
            devices.append({"name": row[0].strip(), "vendor": "nvidia",
                            "vram_gb": float(row[1]) / 1024,
                            "free_vram_gb": float(row[2]) / 1024,
                            "source": "nvidia-smi", "shared_memory": False})
        except ValueError:
            continue
    return devices


def _windows_gpu_memory() -> dict[str, float]:
    """WMI AdapterRAM is a DWORD and truncates modern GPUs. Prefer the QWORD."""
    import winreg

    sizes = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Video") as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                key = winreg.EnumKey(root, index)
                try:
                    with winreg.OpenKey(root, key + r"\0000") as adapter:
                        name = winreg.QueryValueEx(adapter, "DriverDesc")[0]
                        size, kind = winreg.QueryValueEx(adapter, "HardwareInformation.qwMemorySize")
                        if kind == winreg.REG_QWORD and size > 0:
                            sizes[str(name).strip().lower()] = size / GIB
                except OSError:
                    continue
    except OSError:
        pass
    return sizes


def _windows_gpus() -> list[dict]:
    shell = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    output = _run([shell, "-NoProfile", "-NonInteractive", "-Command",
                   "@(Get-CimInstance Win32_VideoController | Select-Object Name,AdapterCompatibility) | ConvertTo-Json -Compress"])
    try:
        rows = json.loads(output)
    except (ValueError, TypeError):
        return []
    sizes = _windows_gpu_memory()
    devices = []
    for row in rows if isinstance(rows, list) else [rows]:
        name = str(row.get("Name", "Unknown GPU"))
        vendor = str(row.get("AdapterCompatibility", "")).lower()
        vendor = "amd" if "amd" in vendor or "advanced micro" in vendor else "intel" if "intel" in vendor else "unknown"
        # Registry values for integrated GPUs can describe an aperture, not
        # independent VRAM. Keep capacity unknown unless clearly discrete.
        discrete = "radeon rx" in name.lower() or "arc a" in name.lower() or "arc b" in name.lower()
        devices.append({"name": name, "vendor": vendor,
                        "vram_gb": sizes.get(name.lower()) if discrete else None,
                        "free_vram_gb": None, "source": "windows-registry" if discrete else "windows-cim",
                        "shared_memory": not discrete})
    return devices


def assess_hardware(storage_path: str = "") -> dict:
    """Return a fresh inventory with explicit missing-probe diagnostics.

    Storage is checked at the selected model directory (nearest existing
    ancestor), not assumed to be the drive containing the Chad checkout.
    """
    warnings = []
    ram = available = None
    cores = None
    try:
        import psutil

        memory = psutil.virtual_memory()
        ram, available = memory.total / GIB, memory.available / GIB
        cores = psutil.cpu_count(logical=False) or psutil.cpu_count()
    except (ImportError, OSError):
        warnings.append("RAM detection unavailable. Enter memory manually before relying on fit estimates.")
    gpus = _nvidia()
    if platform.system() == "Windows":
        windows = _windows_gpus()
        # Keep distinct AMD/Intel adapters, without duplicating NVIDIA rows.
        gpus.extend(gpu for gpu in windows if not any(
            gpu["name"].lower() == known["name"].lower() for known in gpus))
    if not gpus:
        warnings.append("No GPU was detected; CPU-only recommendations remain available. Missing drivers can also hide a GPU.")
    if any(gpu["vram_gb"] is None for gpu in gpus):
        warnings.append("Some GPU memory is unknown or shared. Shared RAM is not added to dedicated VRAM.")
    storage_free = None
    target = Path(storage_path).expanduser() if storage_path else Path.home() / ".lmstudio" / "models"
    try:
        while not target.exists() and target != target.parent:
            target = target.parent
        storage_free = shutil.disk_usage(target).free / GIB
    except (OSError, ValueError):
        warnings.append("Available model storage could not be detected.")
    return {"scope": "local_process_host", "os": platform.system(),
            "cpu": platform.processor() or platform.machine(), "physical_cores": cores,
            "ram_gb": ram, "available_ram_gb": available, "gpus": gpus,
            "storage_free_gb": storage_free, "warnings": warnings}
