"""Stage the pinned Windows assessment helper; never run an upstream installer."""
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]


def prepare(root=ROOT):
    spec = json.loads((root / "config/llmfit.json").read_text(encoding="utf-8"))
    target = root / ".tools/llmfit"
    with urlopen(spec["url"], timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != spec["sha256"]:
        raise ValueError("llmfit archive checksum did not match the pinned release")
    with ZipFile(io.BytesIO(archive)) as bundle:
        exe = [p for p in bundle.namelist() if p.endswith("/llmfit.exe") or p == "llmfit.exe"]
        if len(exe) != 1:
            raise ValueError("llmfit archive must contain exactly one executable")
        target.mkdir(parents=True, exist_ok=True)
        (target / "llmfit.exe").write_bytes(bundle.read(exe[0]))
    # Shipped notice is kept independently of the executable/archive layout.
    (target / "LICENSE.txt").write_text((root / "licenses/llmfit-MIT.txt").read_text(encoding="utf-8"), encoding="utf-8")
    print("Pinned llmfit helper staged and archive checksum verified.")


if __name__ == "__main__":
    prepare()
