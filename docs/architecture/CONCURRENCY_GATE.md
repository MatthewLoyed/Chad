# Turn Concurrency & Background Gating Architecture

**Status:** Enforced  
**Component:** `src/core/orchestration/conversation/concurrency_gate.py`  
**Integration:** `ConversationOrchestrator._process_request_locked`

---

## 1. Problem Statement & Root Cause

In local-first and hybrid AI deployments, the system runs with constrained local resources (e.g., a single consumer GPU serving LM Studio and/or local diffusion via ComfyUI).

Historically, turn synchronization was isolated solely per-session (`lock_key(session_id, delivery)`). When background daemons (such as Gmail push webhooks, Telegram autonomous sessions, heartbeats, or cron jobs) triggered at the same moment an interactive user issued a query:
1. Two independent LangGraph execution engines were instantiated concurrently.
2. Both graphs issued simultaneous model inference and prewarm requests to LM Studio (`bonsai-27b`, `qwen2.5-3b-instruct`, `qwen3.5-9b`).
3. LM Studio experienced severe VRAM thrashing and model swapping delays, resulting in inference latency spikes or 503 errors.
4. Autonomous background turns clobbered interactive responsiveness.

---

## 2. Architectural Design

To solve this, `TurnConcurrencyGate` implements a **priority-aware queueing gate** governing all turn execution entering `ConversationOrchestrator`:

```
                       [ Incoming Turn ]
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
       [ Interactive Turn ]           [ Background Turn ]
      (category='chat'|'agent',      (category='autonomous'|'system',
       initiator=USER, delivery=GUI)   initiator=WEBHOOK|SYSTEM|SKILL)
               │                               │
               │ [HIGH Priority]               │ [LOW Priority]
               ▼                               ▼
   ┌───────────────────────────────────────────────────────────┐
   │                    TurnConcurrencyGate                    │
   │                                                           │
   │ - User turns acquire execution slot immediately.          │
   │ - Background turns wait if ANY user turn is active        │
   │   or waiting in queue.                                    │
   │ - In local mode (is_local=True), enforces single-flight   │
   │   mutual exclusion across ALL turns to protect GPU/VRAM.  │
   └─────────────────────────────┬─────────────────────────────┘
                                 │
                                 ▼
                     [ SessionLockManager ]
                     (Per-session FIFO lock)
                                 │
                                 ▼
                   [ LangGraph Turn Execution ]
```

---

## 3. Guarantees & Contracts

1. **User Turn Primacy**:
   - Interactive user turns are never blocked behind queued background tasks.
   - If a background turn is waiting and an interactive user turn arrives, the user turn takes precedence and executes first.
2. **GPU Mutual Exclusion**:
   - When `ConfigManager.is_local_model_active()` is `True`, at most one turn holds execution rights on the local inference endpoint.
   - Prevents multi-model thrashing and competing model loading.
3. **Deadlock Free Composition**:
   - `TurnConcurrencyGate.turn_guard` is always acquired *before* the per-session `SessionLockManager.get(lock_key)` lock via `AsyncExitStack`.
   - On completion or exception, locks and counters are released via `finally:` blocks and all waiting tasks are notified via `Condition.notify_all()`.

---

## 4. Verification

- Tested via [`tests/unit/test_concurrency_gate.py`](../../tests/unit/test_concurrency_gate.py):
  - User turn runs immediately.
  - Background turns wait for active user turns.
  - User turns preempt queued background turns.
  - Local model turns enforce strict 1-flight mutual exclusion across concurrent coroutines.
