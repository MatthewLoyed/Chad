# Tool Selector Model Benchmark & Optimization Report (October 13, 2026)

## 1. Executive Summary

- **Context**: In live turns using local models with `prism-ml/bonsai-27b` as the primary supervisor, Time To First Token (TTFT) was observed taking 20s to over 90s (median ~35s).
- **Primary Bottleneck Identified**: Telemetry logs (`TurnPerfTracker` and Supabase message metadata) traced **75% to 90%** of the pre-model delay to the **9B tool selector** (`qwen/qwen3.5-9b`). On an active 16 GB VRAM budget, running Bonsai 27B (~4.7 GB) and Qwen 3.5 9B (~6.55 GB) simultaneously with large context lengths and ancillary models caused LM Studio to offload layers to CPU RAM, driving tool selection inference time from ~15s up to **78.5s**.
- **Optimization**: Evaluated candidate models across sub-4B parameter classes to find the optimal trade-off between latency, VRAM footprint, and tool selection accuracy.
- **Resolution**: Switched the tool selector to **`qwen2.5-3b-instruct`**, reducing tool selection latency by **7.4x** (from **15.78s down to 2.12s per query**) while maintaining an **0.80 F1 score** and zero conversational hallucinations. `qwen/qwen3.5-9b` is preserved for `voice` and `contact` front-desk lanes.

---

## 2. Head-to-Head Multi-Model Benchmark Results

Tested across 10 representative queries spanning Canvas LMS, Gmail, Google Calendar, Media/Playback, Smart Home, Multi-step File Archiving, Sandbox Code Execution, Timed Alerts, and Conversational Abstention.

| Metric | Qwen 3.5 9B *(Original)* | Qwen 2.5 3B *(New Default)* | Qwen 2.5 Coder 1.5B | Salesforce xLAM-1b *(Native)* |
| :--- | :--- | :--- | :--- | :--- |
| **Average Latency** | **15.78s** | **2.12s** *(7.4x faster)* | **2.64s** *(6.0x faster)* | **2.58s** *(6.1x faster)* |
| **Total 10-Query Time** | **157.8s** (~2.6m) | **21.2s** | **26.4s** | **25.8s** |
| **Average F1 Score** | **0.87** | **0.80** | **0.62** | **0.58** |
| **Exact Match Rate** | **80.0%** (8/10) | **60.0%** (6/10)* | **30.0%** (3/10) | **50.0%** (5/10) |
| **Conversational Abstention** | **Exact** (`[]`) | **Exact** (`[]`) | **Failed** *(Hallucinated)* | **Exact** (`[]`) |
| **3-Step Compound Tasks** | **Exact** (3/3) | **Exact** (3/3) | **Exact** (3/3) | Missed step 3 (2/3) |
| **VRAM Footprint in LM Studio** | **~6.55 GB** | **~2.10 GB** | **~1.76 GB** | **~1.43 GB** |

*\*Note on Qwen 2.5 3B's functional performance:* On Prompt #3 (*"Check my Gmail inbox"*), the benchmark scored `search_gmail` as ground truth, but Qwen 3B selected `gmail_check_inbox` (a valid Gmail tool in Chad's catalog). On Prompt #2 (*"Check grades and download syllabus"*), it selected both expected tools plus `get_course_syllabus`.

---

## 3. Test Cases & Model Decisions

| # | Prompt Category & User Input | Expected Tools | Qwen 3.5 9B (15.8s) | Qwen 2.5 3B (2.1s) | Salesforce xLAM-1b (2.6s) |
| :- | :--- | :--- | :--- | :--- | :--- |
| **1** | **Canvas Due Date**: *"What assignments do I have due this week in Canvas?"* | `get_canvas_dashboard_todos` | Exact | Exact | Exact |
| **2** | **Canvas Files & Grades**: *"Check my recent grades in Canvas and download syllabus"* | `get_recent_grades`, `download_course_file` | Exact | Both + `get_course_syllabus` | Exact |
| **3** | **Gmail Search**: *"Check my Gmail inbox for any new emails from John"* | `search_gmail` | `search_gmail` + `gmail_check_inbox` | `gmail_check_inbox` | `gmail_check_inbox` |
| **4** | **Compound Email**: *"Draft a reply to Sarah saying I'll attend tomorrow"* | `draft_gmail_reply` | `draft_gmail_reply` + `search_gmail` | Exact | Missed (`[]`) |
| **5** | **App & Volume**: *"Open Spotify and turn down the system volume"* | `open_app`, `control_volume` | Exact | `control_volume` *(missed app)* | Exact |
| **6** | **YouTube & Lights**: *"Mute YouTube playback and set bedroom lights to 40%"* | `youtube_desktop_control`, `hubspace_control` | Exact | `youtube_control` + `volume` | Exact |
| **7** | **Multi-Step File/Email**: *"Find resume, zip it, and email to recruiter"* | `fuzzy_find_local`, `archive_manager`, `send_email` | Exact (3/3) | Exact (3/3) | Missed `send_email` (2/3) |
| **8** | **Code Execution**: *"Run a Python script to calculate fibonacci numbers"* | `run_sandboxed_python` | Exact | Exact | Missed (`[]`) |
| **9** | **Timed Reminder**: *"Remind me in 45 minutes to take a break"* | `set_reminder` | Exact | Exact | Missed (`[]`) |
| **10** | **Conversational**: *"Explain how backpropagation works"* | `[]` *(Zero tools)* | Exact (`[]`) | Exact (`[]`) | Exact (`[]`) |

---

## 4. Key Architectural Findings

### 1. Why Salesforce xLAM-1b Struggled initially
- **Template Sensitivity**: xLAM-1b is an Action Model trained strictly on the APIGen layout (`[BEGIN OF TASK INSTRUCTION]`, `[BEGIN OF AVAILABLE TOOLS]`, etc.). When fed standard selector prompts, accuracy was only 10%. With its native layout, it reached 50%.
- **Parameter Refusal**: xLAM refuses tool calls if expected arguments are not pre-supplied in the query (e.g. refusing to call `draft_gmail_reply` because the email body was not in the user query).

### 2. Why Qwen 2.5 Coder 1.5B Over-Selected
- **Insufficient Parameter Discrimination**: At 1.5B parameters, the model lacked the restraint needed for negative constraints, hallucinating tools on general conversation and picking 2–3 alternative tools per task.

### 3. Why Qwen 2.5 3B Hits the Sweet Spot
- **Ultra-Fast Inference**: **2.12s average latency** per query (down from 15.78s on 9B).
- **Low VRAM Impact**: **2.10 GB** in VRAM allows `prism-ml/bonsai-27b` and `qwen2.5-3b-instruct` to coexist in 16 GB VRAM without layer offloading to CPU RAM.
- **Accurate Compound Routing**: Successfully chained 3 tools in compound requests (fuzzy file search + archive compression + email dispatch).

---

## 5. Configuration Applied

In `config/config.yaml`:
```yaml
tools:
  llm_selector_model: qwen2.5-3b-instruct
  llm_selector_fail_fast: true
  hybrid_selection: true
  hybrid_shortlist_k: 16

local_models:
  lifecycle:
    model_size_estimates_gb:
      "1b": 1
      "3b": 2.5
      "9b": 6
  category_models:
    voice: qwen/qwen3.5-9b       # UNCHANGED: 9B preserved for voice fast-path
    contact: qwen/qwen3.5-9b     # UNCHANGED: 9B preserved for phone/telegram front desk
```

In `config/preferences.yaml`:
```yaml
local_models:
  lifecycle:
    pinned_models:
    - gemma-4-26b
    - qwen3.5-9b                 # UNCHANGED: 9B stays pinned for voice/contact
```
