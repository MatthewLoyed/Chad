# scripts/

Developer tooling for diagnosis, benchmarking, tracing, and data inspection. **Not part of the production runtime** — these are run manually by developers.

Run from repo root with `PYTHONPATH="." .\.venv\Scripts\python.exe scripts/<name>.py`.

---

## Trace & Pipeline Probing

| Script | Purpose |
|---|---|
| `trace_probe.py` | **Primary probe tool** — real WebSocket single/multi-turn probes capturing reasoning stream + tool calls |
| `perfect_trace.py` | LangSmith trace perfection / audit |
| `analyze_trace.py` | Regex-based finding codes over captured traces |
| `fetch_trace.py` | Pull a specific trace by ID |
| `debug_last_loop.py` | Pull LangSmith traces after an infinite loop stall |
| `debug_prompt_pipeline.py` | Inspect prompt payloads mid-pipeline |

## Benchmarking

| Script | Purpose |
|---|---|
| `benchmark_pipeline.py` | End-to-end pipeline latency benchmark |
| `benchmark_gui_boot.py` | GUI WebView2 boot time benchmark |
| `measure_total_start.py` | Total startup time measurement |

## Diagnostics

| Script | Purpose |
|---|---|
| `diagnose_startup_hang.py` | Identify what blocks server startup |
| `diagnose_steps.py` | Step-by-step orchestration diagnostics |
| `diagnose_supabase_latency.py` | Supabase round-trip latency profiling |
| `verify_reasoning.py` | Verify model reasoning/thinking tokens |
| `verify_on_thinking.py` | Verify thinking token gating |
| `verify_order_hypothesis.py` | Ordering hypothesis in tool results |
| `verify_gui_boot.py` | GUI boot sequence verification |
| `verify_tool_selection.py` | Tool selection live test |
| `verify_embedding_model.py` | Embedding model load/inference validation |

## Memory & RAG

| Script | Purpose |
|---|---|
| `evaluate_rag_baseline.py` | RAG recall baseline evaluation |
| `compare_recall_mechanics.py` | Compare RAG recall strategies |
| `inspect_cloud_memories.py` | Browse Supabase memories table |
| `inspect_local_memories.py` | Browse local memory store |
| `inspect_errors.py` | Scan recent error logs |
| `oats_refine.py` | OATS (on-device tool selector) refinement loop |

## Google / Chrome Sync Verification

| Script | Purpose |
|---|---|
| `verify_chrome_ingest.py` | Chrome history ingestor smoke test |
| `verify_google_ingest.py` | Google workspace ingestor smoke test |
| `verify_real_chrome_sync.py` | Live Chrome sync integration test |
| `verify_all_sources_sync.py` | Multi-source sync combined test |
| `verify_supabase_sync_integration.py` | Supabase sync integration |
| `verify_memory_pipeline.py` | Full memory ingest pipeline test |
| `verify_pipeline_leak.py` | Detect context/memory leaks |

## Handoff & Context Verification

| Script | Purpose |
|---|---|
| `verify_handoff_context.py` | Verify context passed at agent handoff |
| `verify_handoff_tool_execution.py` | Verify tool execution after handoff |

## Tool & Config Management

| Script | Purpose |
|---|---|
| `sync_tool_preambles.py` | Sync tool description preambles to registry |
| `list_google_drive_mcp_tools.py` | List Drive MCP tool definitions |
| `list_registered_tools.py` | List all tools in the registry |
| `list_runs.py` | List eval run history |
| `restart_server.py` | Restart the Brain dev server |

## maintenance/

Run occasionally; most are destructive — read before running.

| Script | Purpose |
|---|---|
| `kill_chad_zombies.ps1` | **Run after dev sessions** — kills orphaned cmd/node processes |
| `cleanup_agent_skills.py` | Remove stale agent skill entries |
| `dump_supabase_tools.py` | Export Supabase tool table to JSON |
| `nuke_supabase_tools.py` | **Destructive** — wipe Supabase tools table |
| `restore_mcp_tools.py` | Re-populate MCP tool entries from local definitions |
| `restore_playwright.py` | Re-register Playwright MCP tools |
| `sync_skills_to_supabase.py` | Push local skill definitions to Supabase |
| `../../src/scripts/refresh_rag_tools.py` | Sync tool registry to Supabase — scans MCP servers live first, so it sees Notion/Google Workspace/etc. tools. Use this, not a filesystem-only sync (that class of script deleted ~89 live MCP tools from prod on 2026-07-05 because it never saw them). |
