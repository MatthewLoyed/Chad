# 🧪 Test Suite

`tests/unit/` (100+ files, 750+ tests) is the primary suite and runs in full on every PR via
`.github/workflows/evals.yml` — pure-mocked, no server/GPU/LM Studio required. The other folders
below are for scenarios that need real infrastructure and aren't part of the CI gate (see #216).

## 📂 Master Structure

| Folder | Purpose | Key Test |
| :--- | :--- | :--- |
| [**`unit/`**](file:///tests/unit/) | Core logic verification. Runs in CI on every PR. | `test_langgraph_engine.py` |
| [**`scenarios/`**](file:///tests/scenarios/) | **End-to-End** repeatable flows. | `test_heartbeat_cron_flow.py` |
| [**`integration/`**](file:///tests/integration/) | High-level system integration (needs a running Brain/services). | `test_pipeline_e2e.py` |
| [**`manual/`**](file:///tests/manual/) | Hardware & Credential checks. | `verify_rag_results.py` |
| [**`support/`**](file:///tests/support/) | Automation runners. | `run_e2e_tests.py` |

---

## 🚀 Running Tests

### 1. Unit suite (recommended default — no running server needed)
```bash
pytest tests/unit/
```
Two files (`test_app_controller.py`, `test_security_hardening.py`) are Windows-only (pywebview /
sounddevice) and are skipped automatically on non-Windows platforms via `tests/unit/conftest.py`.

### 2. The Master E2E Suite
Run the automated end-to-end suite which auto-detects your API keys:
```bash
python tests/support/run_e2e_tests.py
```

### 3. Tool Exposure (RAG)
Verify that the cloud model "sees" the right tools for a given query:
```bash
python tests/manual/verify_rag_results.py
```

### 4. Hardware/Connection Diagnostics
```bash
python tests/manual/diagnose_audio.py         # Test Mic/Audio
python tests/manual/verify_limb_connection.py  # Test Brain-Limb Bridge
python tests/manual/verify_gmail_auth.py      # Test Google Workspace
```

---

## 🤖 Guidance for AI Assistants
- **Prefer `tests/unit/`** for new coverage — it's mocked, fast, and gated in CI. Only reach for
  `integration/`/`manual/` when the thing under test genuinely needs live infra.
- **Delete one-off scripts.** If a script is created for a specific debug session, place it in `scratch/`, not here.
- **Keep `tests/unit/` green.** If you add a test that depends on ambient state (cwd, `$HOME`,
  a real `.env` var, execution order), it will eventually fail in CI even if it passes locally —
  see the fixture patterns in `test_config_manager.py` and `conftest.py` for how to isolate state.
