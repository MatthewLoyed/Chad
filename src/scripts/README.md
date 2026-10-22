# src/scripts/

Dev-infrastructure scripts: server launchers, data migrations, setup utilities, and lightweight diagnostics. **Not part of the production import graph.**

Run from repo root: `PYTHONPATH="." .\.venv\Scripts\python.exe src/scripts/<name>.py`

---

## Dev Server

| Script | Purpose |
|---|---|
| `dev_server.py` | Start Brain server with ngrok tunnel + hot-reload (primary dev entry point) |
| `boot_chad.vbs` | Windows VBS launcher for headless startup |
| `run_dev.bat` | Batch wrapper for dev server |

## Setup & Configuration

| Script | Purpose |
|---|---|
| `setup_telegram_webhook.py` | Register Telegram webhook URL |
| `generate_token.py` | Generate API/auth tokens |
| `list_gemini_models.py` | List available Gemini model IDs |

## Data Migrations (run once)

| Script | Purpose |
|---|---|
| `migrate_to_supabase.py` | Migrate local data to Supabase |
| `migrate_personal_index.py` | Migrate personal index to Supabase vector store |
| `migrate_persona_to_cloud.py` | Migrate persona config to cloud |
| `populate_supabase_tools.py` | Initial population of Supabase tools table |

## RAG & Tool Registry

| Script | Purpose |
|---|---|
| `refresh_rag_tools.py` | Re-index tools in Supabase RAG |
| `probe_supabase.py` | Quick Supabase connectivity + query test |
| `diagnose_semantics.py` | Semantic selector diagnostic (query → top tools) |

## Verification

| Script | Purpose |
|---|---|
| `verify_fixes.py` | Smoke-check recent bug fixes |
| `verify_organize_skill.py` | Verify the organize-skill pipeline |
