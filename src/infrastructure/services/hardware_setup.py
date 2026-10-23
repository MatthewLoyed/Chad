"""Compose local assessment and offline ranking for the desktop setup bridge."""
from src.core.tools.failure import failure, BAD_INPUT
from src.infrastructure.services.hardware_assessment import assess_hardware
from src.infrastructure.services.model_recommendations import number, recommend


def assess(storage_path="", context=8192, manual=None):
    try:
        hardware = assess_hardware(storage_path)
        manual = manual or {}
        if not isinstance(manual, dict):
            raise ValueError("Manual hardware must contain numeric memory values.")
        for key in ("ram_gb", "storage_free_gb", "vram_gb"):
            if key not in manual or manual[key] in (None, ""):
                continue
            value = number(manual[key])
            if value is None:
                raise ValueError("Manual memory values must be finite, nonnegative GiB.")
            if key == "vram_gb":
                hardware["gpus"] = [{"name": "Manual GPU", "vram_gb": value,
                                     "free_vram_gb": None, "shared_memory": False, "source": "manual"}]
            else:
                hardware[key] = value
                if key == "ram_gb":
                    hardware["available_ram_gb"] = None
        hardware["manual"] = bool(manual)
        if manual:
            hardware["warnings"].append("Manual values replace detected capacity; they do not add memory. Rescan without overrides to restore detection.")
        return recommend(hardware, context)
    except (TypeError, ValueError):
        return failure("Check manual RAM/VRAM/storage values and choose a supported context length.", failure_class=BAD_INPUT)
