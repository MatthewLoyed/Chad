---
name: pwa-mobile-review
description: >-
  Capture and visually review Chad's phone PWA (iPhone viewport) via Playwright
  screenshots. Use when checking mobile/PWA UI bugs, safe-area layout, drawer
  chrome, connection status, or verifying frontend fixes on the Dev Tailscale
  PWA / Vite against the Dev Brain.
---

# PWA mobile visual review

## Mental model (do not confuse these)

| Surface | URL | What it is |
|---|---|---|
| **Phone PWA** | `https://chad.tailed0bed.ts.net` | Tailscale Serve → Dev Brain `:8788`, which serves **built** `dist/` from **AIAssistant-dev** |
| **Chad Dev Vite** | `http://127.0.0.1:5173` (or the worktree port) | HMR source — use this to verify **uncommitted UI edits** against Dev Brain |
| **Prod Brain** | `:8767` | Sacred — connect-only; never restart for UI work |

Vite is **not** what the installed iPhone PWA loads. The phone loads Brain `StaticFiles(dist)`. Vite is the agent/iteration loop for UI changes.

`transportConfig.defaultServerUrl()` maps `hostname:8767`. When using Vite, always seed `localStorage` `chad.serverUrl` to the Dev Brain (`http://127.0.0.1:8788` or `https://chad.tailed0bed.ts.net`).

## Preconditions

1. Dev Brain healthy: `GET http://127.0.0.1:8788/health` → `ok`
2. Tailscale Serve (phone): `tailscale serve status` should show `/` → `http://127.0.0.1:8788`
3. Token: `CHAD_SERVER_TOKEN` from `AIAssistant-dev\.env` or main `.env` (never print it)
4. Playwright Chromium available in `src/clients/web` (`npx playwright install chromium` once)
5. Prefer verifying on **AIAssistant-dev's Vite (`:5173`)** when that stack is already running (Chad Dev). A fresh worktree Vite may miss deps / fail to hydrate — don't confuse a broken local Vite with a product bug.

## Capture workflow (Playwright + iPhone 13)

Browser MCP may be missing; Playwright is the reliable path.

1. Prefer **Vite from the worktree you're editing** (e.g. `--port 5174`) so screenshots reflect your patch. Use Tailscale only when validating the **built** PWA after `npm run build` in **AIAssistant-dev**.
2. Run a small node script (ESM) from `src/clients/web`:

```js
import { chromium, devices } from "@playwright/test";

const out = process.env.OUT;           // screenshot dir
const token = process.env.CHAD_TOKEN;  // from .env, never log
const appUrl = process.env.APP_URL;    // Vite or Tailscale origin
const brainUrl = process.env.BRAIN_URL || "https://chad.tailed0bed.ts.net";

const browser = await chromium.launch();
const context = await browser.newContext({ ...devices["iPhone 13"] });
const page = await context.newPage();
await page.goto(appUrl + "/", { waitUntil: "domcontentloaded" });
await page.evaluate(({ token, brainUrl }) => {
  localStorage.setItem("chad.serverUrl", brainUrl);
  localStorage.setItem("chad.token", token);
}, { token, brainUrl });
// Fill LoginScreen if still shown, click Connect, wait for shell
await page.waitForTimeout(4000);
await page.screenshot({ path: `${out}/home.png` });
const toggle = page.locator(".chad-mobile-nav-toggle");
if (await toggle.count()) {
  await toggle.click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${out}/nav-open.png` });
}
await browser.close();
```

3. **Read the PNGs** with the Read tool (vision) — do not trust text-only DOM dumps for layout bugs.
4. Assert the specific symptoms (e.g. no Connected chip when idle; no X when drawer open).

### PowerShell token load (do not echo token)

```powershell
$envPath = "C:\Users\ExampleUser\Documents\Coding\Fun\AIAssistant-dev\.env"
$env:CHAD_TOKEN = (Select-String -Path $envPath -Pattern '^CHAD_SERVER_TOKEN=(.*)$').Matches.Groups[1].Value.Trim().Trim('"')
$env:OUT = "$env:TEMP\chad-pwa-mobile"
$env:APP_URL = "http://127.0.0.1:5174"   # or https://chad.tailed0bed.ts.net
$env:BRAIN_URL = "https://chad.tailed0bed.ts.net"
```

## What to look for on mobile

- Drawer: hamburger when closed; **backdrop** (not an X over the rail) to dismiss
- Connection chip: toast on connect/reconnect/error only — **not** persistent "Connected"
- Safe-area / composer / suggested-action clipping
- Sections default expand state (Categories should start collapsed)

## After UI fixes ship to the phone

1. Land/sync changes into **AIAssistant-dev** (Chad Dev worktree)
2. `cd src/clients/web && npm run build` there (or re-click **Chad Dev** if that stack rebuilds)
3. Hard-refresh / re-open the iOS PWA (service worker may cache the shell)
4. Re-run this capture against Tailscale to confirm
