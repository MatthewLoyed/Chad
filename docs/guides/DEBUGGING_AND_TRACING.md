# Debugging & Turn Tracing Playbook

This playbook documents how to trace and diagnose Chad's end-to-end execution turns when an agent gets stuck, talks instead of using tools, hallucinates, or drops critical capabilities.

---

## 1. Where Telemetry and Logs Live

| Artifact | Path | Purpose |
|---|---|---|
| **Brain Daily Logs** | `data/dev_logs/brain_YYYYMMDD.log` | Complete pipeline, tool selector, supervisor, sub-agent handoffs, errors. |
| **Outbound Prompt Dumps** | `docs/logs/litellm_prompt_dump.txt` | Exact text, tool schemas, and messages sent to LLM backends (LiteLLM / LM Studio / Ollama / Cloud). |
| **Tool Registry Cache** | `data/tool_cache.json` | Schemas and file paths for fast startup and stub hydration. |
| **Local Checkpoints** | `data/local_langgraph_checkpoints.db` | Conversation turn state persisted across turns. |

---

## 2. Essential PowerShell Tracing Commands

Run these from the repo root in PowerShell:

### A. Trace Tool Selection (`TurnToolSelector`)
Find what the 9B model picked vs what bundles expanded:
```powershell
Get-Content data/dev_logs/brain_YYYYMMDD.log | Select-String "TOOL_SELECT"
```
**Key entries to inspect:**
* `[ TOOL_SELECT ] LLM selected N tools: [...]` — The primary tools picked by the selector model.
* `[ TOOL_SELECT ] After bundle expansion: [...]` — Tools added by intent or domain bundles.
* `[ TOOL_SELECT ] Final tool set (N): [...]` — The exact schemas packaged for the turn.

### B. Trace Bound Tools on the Supervisor (`ChadSupervisor`)
Check what tools were actually bound into the model's runtime context:
```powershell
Get-Content data/dev_logs/brain_YYYYMMDD.log | Select-String "Tools in state"
# Or final bindings:
Get-Content data/dev_logs/brain_YYYYMMDD.log | Select-String "Final Bound Tools"
```
> [!IMPORTANT]
> If a tool was selected during `TOOL_SELECT` but is missing from `Tools in state`, check `stabilize_local_tools` or checkpointer state merges.

### C. Slice by Turn Timestamp
When debugging a turn from a specific time (e.g. 23:49):
```powershell
Get-Content data/dev_logs/brain_YYYYMMDD.log | Select-String "23:49:" | Select-Object -First 50
```

### D. Diagnose "Talking Instead of Acting" (`NarrationGuard`)
If Chad says "I will search for X" or "Let me read assignment Y" without calling a tool:
```powershell
Get-Content data/dev_logs/brain_YYYYMMDD.log | Select-String "NARRATION_GUARD"
```
* Shows if `NarrationGuard` intercepted promises of action and forced a nudge/resend.

### E. Trace Sub-Agent Delegation & Routing
Check why the supervisor handed off to Deep Research, Code Agent, or Traversal:
```powershell
Get-Content data/dev_logs/brain_YYYYMMDD.log | Select-String "route_after_supervisor"
# Or search for delegation tool calls:
Get-Content data/dev_logs/brain_YYYYMMDD.log | Select-String "delegate_to_"
```

### F. Check Stub Hydration / Dynamic Import Failures
Check if a tool failed to load from disk or collided with Python built-ins:
```powershell
Get-Content data/dev_logs/brain_YYYYMMDD.log | Select-String "Failed to hydrate"
```

---

## 3. Real Anatomy of a Failure: Case Study (Canvas Assignment 4)

1. **LLM Selector:** Correctly picked `get_assignment_details` + `write_file`.
2. **Domino Expansion:** `write_file` was in multiple visual bundles containing `load_skill`, which transitively pulled in all other bundles (47 tools total).
3. **Sticky Tool Slicing:** `stabilize_local_tools` had an LRU cap of 20 and popped 27 tools from the front (including `get_assignment_details` and `get_course_files`).
4. **Missing Tool in Context:** Supervisor had only 20 trailing tools (diagrams, music, OSM maps).
5. **Narration & Delegation:** Model explained its intent in prose -> `NarrationGuard` nudged it -> lacking local assignment tools, it delegated to `deep_research_agent`.
6. **Hallucinated Public Web Search:** Deep Research searched Google/Serper for internal MSU student assignments behind SSO and looped.

**Lesson:** Always check `[ TOOL_SELECT ] Final tool set` against `[ SUPERVISOR ] Turn starting. Tools in state:` to ensure critical tools are not dropped between selection and model invocation.
