"""Read LM Studio's local file inventory without loading models or uploading hardware."""
import json
import subprocess
from pathlib import Path
from src.infrastructure.services import lmstudio_assessment as lm
from src.infrastructure.services.model_recommendations import catalog


def installed_assets(storage_path="", *, verify=False):
    result = {}
    try:
        cli = lm.find_cli()
        rows = json.loads(lm._cli([cli, "ls", "--json"]).stdout) if cli else []
        for model in catalog():
            root = Path(storage_path).expanduser() if storage_path else Path.home() / ".lmstudio" / "models"
            file = root / model["repo"] / model["filename"]
            if not file.is_file() or file.stat().st_size != model["size_bytes"]:
                continue
            if verify:
                from src.infrastructure.services.setup_assets import _verified
                if not _verified(file, model):
                    continue
            expected = f"{model['repo']}/{model['filename']}".lower()
            for row in rows:
                # Exact path+size, never a family/quantization/name guess. Integrity
                # is verified again by the download job before skipping acquisition.
                if (row.get("type") == "llm" and row.get("path", "").replace("\\", "/").lower() == expected
                        and row.get("sizeBytes") == model["size_bytes"]):
                    result[model["id"]] = row["modelKey"]
            if file.is_file() and file.stat().st_size == model["size_bytes"]:
                result.setdefault(model["id"], f"{model['repo']}/{model['filename']}")
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        pass  # Unknown inventory never masquerades as a known installed asset.
    return result
