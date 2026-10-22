"""Launch chad-buddy from the old chad-hud path."""

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / "chad-buddy"
print("[chad-hud] Desktop buddy moved to chad-buddy/. Launching there…", flush=True)
sys.path.insert(0, str(_ROOT))
runpy.run_path(str(_ROOT / "main.py"), run_name="__main__")
