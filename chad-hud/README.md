# Chad HUD (Electron command bar)

Alt+Space overlay command bar — **separate** from the desktop buddy pet.

> **Desktop buddy** lives in [`../chad-buddy/`](../chad-buddy/). Run `python chad-buddy/main.py` (or `python chad-hud/main.py` — deprecated shim).

## Run (command bar HUD)

```powershell
cd chad-hud
npm install
npm start
```

Requires the web UI dev server for the React HUD bundle:

```powershell
cd src/clients/web
npm run dev
```

The HUD loads `http://localhost:5173/?mode=hud`.

## Not the buddy

`chad-hud/main.py` forwards to `chad-buddy/main.py`. The GUI (`main_gui.py`) no longer auto-spawns the Electron HUD.

Buddy state is pushed from the Brain via **`GET /buddy/stream` (SSE)** on change. `GET /buddy/state` remains for debug snapshots. See [`../chad-buddy/README.md`](../chad-buddy/README.md).
