# Connectors Setup Guide

Chad ships with optional connectors — MCP tool servers and native integrations (Gmail, Telegram). **None are required.** A fresh install runs fine with every connector greyed out in **Settings → MCP Servers → Connectors**.

Use the in-app wizard (test → pass → save) or follow this doc. Credentials always go in `config/secrets.yaml` (gitignored), never in tracked `config.yaml`.

See also: [Services vs MCP add-ons](../guides/SERVICES_AND_MCPS.md) · [Cold start](COLD_START.md)

---

## Quick reference

| Connector | Type | Credential | Where it goes | In-app wizard |
|-----------|------|------------|---------------|---------------|
| GitHub | MCP | Personal Access Token | `GITHUB_PERSONAL_ACCESS_TOKEN` in `secrets.yaml` | Yes |
| Brave Search | MCP | API key | `BRAVE_API_KEY` | Yes |
| Todoist | MCP | API token | `TODOIST_API_TOKEN` | Yes |
| Notion | MCP | Integration token | `NOTION_API_TOKEN` | Yes |
| Google Drive | MCP | OAuth JSON paths | `GOOGLE_DRIVE_OAUTH_CREDENTIALS`, `GOOGLE_DRIVE_MCP_TOKEN_PATH` | Yes |
| Playwright | MCP | None (spawns browser) | — | Yes (connection test) |
| Web Fetch | MCP | None | — | Yes |
| Excalidraw | MCP | None (needs Docker sidecar) | Start canvas: `docker compose -f docker/chad-services.yml --profile excalidraw up -d` | Yes |
| Supabase | MCP | Browser OAuth | Via `mcp-remote` on first connect | Yes |
| **Canva** | MCP | Browser OAuth | Via `mcp-remote` → `https://mcp.canva.com/mcp` | Yes |
| **Gmail** | Native | Google OAuth | `config/credentials.json` + `config/token.json` | Yes |
| **Telegram** | Native | Bot token | `telegram.bot_token` in `secrets.yaml` | Yes |
| **Canvas LMS** | Native | Instance URL + access token | `CANVAS_BASE_URL`, `CANVAS_TOKEN` in `secrets.yaml` | Yes |
| **Stock Images** | Native | Optional Pexels / Unsplash / Pixabay keys | `PEXELS_API_KEY`, `UNSPLASH_ACCESS_KEY`, `PIXABAY_API_KEY` | Yes |

**Not connectors (optional services):** Presenton, SearXNG, Kroki, **Openverse** — see **Settings → Services** and [SERVICES_AND_MCPS.md](../guides/SERVICES_AND_MCPS.md).

**OpenStreetMap places/maps** are native tools (`geocode`, `search_places`, `get_route`, `show_map`) — free public OSM endpoints, **no API key and no Connectors tile**. Load `osm-maps` skill for the workflow. Optional FastMCP entry: `python -m src.mcp_servers.maps` (also in repo `.mcp.json` as `chad-maps`) for Claude Desktop; do not add it under Chad `mcp_servers` (native tools already cover the Brain).

**Openverse** needs no API key. It powers `search_images` for Creative Commons art and shows as **Openverse (image search)** under Services → Infrastructure Health. Optional stock keys above unlock Pexels/Unsplash/Pixabay on top of Openverse; image fallbacks after that follow the same Default Search backend as `web_search` (SearXNG / Brave / Serper).

**Default web search:** Chad's built-in `web_search` tool uses self-hosted SearXNG (no API key) with Brave API fallback. The **Brave Search MCP** is a separate opt-in connector for MCP-native search tools.

---

## First-time setup

1. Copy secrets template:
   ```powershell
   copy config\secrets.yaml.example config\secrets.yaml
   ```
2. Open Chad → **Settings → MCP Servers → Connectors**.
3. Click a grey **Not connected** tile → follow the wizard.
4. Every connector requires a **connection test to pass** before it is enabled.

Chad will not spawn MCP processes for disabled or unconfigured connectors (no log spam on missing API keys).

---

## MCP connectors

### GitHub
- Create a PAT at [github.com/settings/tokens](https://github.com/settings/tokens) with `repo` scope.
- Wizard field: `GITHUB_PERSONAL_ACCESS_TOKEN`
- Skill: `load_skill('github')` before complex GitHub workflows.

### Brave Search
- API key from [brave.com/search/api](https://brave.com/search/api)
- Wizard field: `BRAVE_API_KEY`

### Todoist
- Token from Todoist → Settings → Integrations → Developer
- Wizard field: `TODOIST_API_TOKEN`

### Notion
- Integration token from [notion.so/my-integrations](https://www.notion.so/my-integrations)
- Share pages/databases with your integration.
- Wizard field: `NOTION_API_TOKEN`

### Google Drive (MCP)
- Download OAuth client JSON from Google Cloud Console.
- Set paths in wizard (saved to `secrets.yaml`):
  - `GOOGLE_DRIVE_OAUTH_CREDENTIALS`
  - `GOOGLE_DRIVE_MCP_TOKEN_PATH`
- Complete OAuth flow for the MCP token file.

### Playwright
- No API key. Spawns a dedicated Chad browser profile (`%LOCALAPPDATA%\ChadBrowser\UserData`).
- Off by default — enable via wizard after connection test passes.
- Heavyweight; use built-in `web_search` for quick lookups.

### Web Fetch
- No API key. Fetches and parses page text via `mcp-fetch-server`.
- Off by default.

### Excalidraw
- No API key. Requires the canvas sidecar:
  ```powershell
  docker compose -f docker/chad-services.yml --profile excalidraw up -d
  ```
- Skill: `load_skill('excalidraw')` before whiteboard tools.

### Supabase
- Browser OAuth via `mcp-remote` on first connect (up to ~2 minutes).
- Separate from upgrading Chad's memory/history to Supabase (#416).

### Canva
- Browser OAuth via `mcp-remote` → `https://mcp.canva.com/mcp` (same Connectors wizard flow as Supabase).
- Free Canva accounts can generate/edit/export; monthly AI allowance applies. Resize and premium exports need Pro+. Autofill/brand templates need Enterprise.
- Skill: `load_skill('canva')` before multi-step design work.
- **Not** Canvas LMS — that is the separate native **Canvas** connector.

---

## Native connectors

### Gmail (Google Workspace)
Gmail uses native tools (`search_gmail`, `read_gmail`, `send_email`, etc.), not an MCP server.

1. **Google Cloud project** with Gmail API enabled.
2. Download OAuth client secrets → save as `config/credentials.json`.
3. **Settings → Connectors → Gmail → Connect** — completes browser OAuth, writes `config/token.json`.
4. **Optional — push notifications:** see [WEBHOOK_SETUP_GUIDE.md](WEBHOOK_SETUP_GUIDE.md) (Gmail Pub/Sub section).

Full workspace skill: `load_skill('google_workspace')`.

### Telegram
1. Create a bot via [@BotFather](https://t.me/BotFather) → `/newbot`.
2. **Settings → Connectors → Telegram** → paste bot token (validated via Telegram `getMe`).
3. Saved to `secrets.yaml`:
   ```yaml
   telegram:
     bot_token: "your-token"
     webhook_secret_token: "long-random-string"   # recommended
     allowed_user_ids: [YOUR_NUMERIC_USER_ID]     # fail-closed; required for inbound
     allowed_chat_ids: [YOUR_NUMERIC_USER_ID]
   ```
4. **Webhook** (Brain reachable over HTTPS): see [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md).
   Prefer Tailscale/private tunnel for day-to-day; ngrok is fine for short tests.

### Canvas LMS
School coursework integration — assignments, grades, syllabus, and dashboard to-dos.

1. In your browser, open your school's Canvas site and copy the base URL
   (e.g. `https://canvas.university.edu` — no `/api/v1` suffix needed).
2. Canvas → **Account** → **Settings** → **Approved Integrations** → **+ New Access Token**.
   Name it "Chad", generate, and copy the token (shown once).
3. **Settings → Connectors → Canvas** → paste base URL + token → **Test connection**.
4. Saved to `secrets.yaml`:
   ```yaml
   CANVAS_BASE_URL: "https://your-school.instructure.com"
   CANVAS_TOKEN: "your-token"
   CANVAS_CONTACT_EMAIL: "user@example.com"  # optional
   ```

Until connected, all Canvas tools stay hidden (no log spam).

---

## Disconnecting

- **MCP:** Connectors wizard → **Disconnect**, or **Installed** tab → disable/remove.
- **Native:** Connectors wizard → **Disconnect** (Telegram token cleared; Gmail token kept but connector disabled).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Connector tile stays grey | Open wizard, complete test; check `secrets.yaml` |
| MCP "0 tools" in logs | Connector disabled or creds missing — expected when opt-in |
| Excalidraw tools missing | Start excalidraw Docker profile on port 3000 |
| Canva OAuth / 0 tools | Re-run Connectors → Canva; browser must complete Canva login; wait up to ~2 min for mcp-remote. If Canva blocks the client, their MCP waitlist may apply — see https://www.canva.dev/docs/mcp/ |
| Gmail "not configured" | `config/credentials.json` + run Connect OAuth |
| Telegram send fails | Verify token; check `telegram.bot_token` in secrets |

---

## Future: in-chat connector flow

When Chad needs an unconfigured tool, it should say *"Gmail isn't connected yet"* and deep-link to the connector wizard ([#217](https://github.com/MatthewLoyed/AIAssistant/issues/217) stretch). Tool messages like `send_telegram_message` already return a not-configured hint.
