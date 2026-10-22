# Chad Desktop Buddy

Standalone desktop pet — **not** the Electron command-bar HUD (`chad-hud/`).

## Run

```powershell
pip install -r chad-buddy/requirements.txt
python chad-buddy/main.py
```

With the project venv + Brain server:

```powershell
# Terminal 1 — Brain
$env:PYTHONPATH="." ; .\.venv\Scripts\python.exe src\remote\server.py

# Terminal 2 — Buddy
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe chad-buddy/main.py
```

## Architecture

```
Brain EventBus  →  src/buddy/BuddyEngine  →  GET /buddy/stream (SSE)
                                                      ↓
                                            chad-buddy/main.py
                                                      ↓
                                            ui/app.js (modes + cues + bubble)
```

### Sustained modes

| Mode | Source |
|------|--------|
| `thinking` | Turn in progress |
| `working` | Tool running |
| `error` | Failed tool / `ErrorOccurred` |
| `idle` | Turn complete |
| `sleeping` | Local — 60s no mouse when Brain idle |

### One-shot cues

Rules can emit a `cue` (e.g. `success` after any successful tool). The client plays the mapped SVG then restores the current mode.

## Adding animations

Register a `BuddyRule` on the Brain (startup hook, plugin, or test):

```python
from src.buddy import BuddyEngine, BuddyRule
from src.application.events import ToolCompleted

engine.register_rule(BuddyRule(
    name="email_sent",
    events=(ToolCompleted,),
    match=lambda e: e.success and e.tool_name in ("send_email", "gmail_send"),
    cue="success",
    cue_duration_s=2.0,
    meta_from_event=lambda e: {"tool": e.tool_name},
))
```

Register matching SVG art in `ui/app.js` via `CUE_TO_SVG` (or add `chad-success.svg` and load it in `main.py`).

## Ambient observations (optional)

Enable in `config/config.yaml`:

```yaml
buddy:
  observer:
    enabled: true
    model: qwen/qwen3.5-9b
    interval_minutes: 30
```

Requires Brain server + Hands daemon. Chad glances at your screen on a timer, uses a **thin persona slice** (`identity` + `user` from Supabase) so comments feel personal, and shows a speech bubble on the pet.

Persona rows are pulled from `PromptRegistry` — same source as main Chad, but truncated (~900 chars) plus a small buddy-task suffix.

- Double-click → poke
- Right-click → quit
- Drag anywhere on the window
