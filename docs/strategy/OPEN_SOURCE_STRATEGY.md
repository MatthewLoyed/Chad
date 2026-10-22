# Chad — Open-Source Strategy, Competitive Research & Parallel Execution Plan

_Last updated: 2026-07-09. Owner: Matthew. Living document — the source of truth for
"why are we open-sourcing Chad, what makes it worth starring, and what do we build next."_

> **TL;DR / The Bet:** Chad is a **local-first desktop AI that gets big-model task
> performance out of small local models (9B–26B class)** by investing in the *harness*
> — multi-agent orchestration, semantic tool discovery, skills — instead of a paid API
> or an unrealistic 130GB model. It runs on your machine, its data stays on your machine,
> and it can actually control your computer. That is a genuinely different product from
> every viral agent of 2026, all of which are cloud-key-wired messaging gateways.

---

## 1. The competitive landscape (2026)

Research pass across the three assistants Matthew named plus the broader field.
Full sources at the bottom.

| Project | What it actually is | Stars | Shape |
|---|---|---|---|
| **OpenClaw** (Steinberger) | Personal AI reachable from 24+ chat apps (WhatsApp/Telegram/Signal/iMessage…). Runs on your devices. | ~210k–346k (fastest repo to 100k in GitHub history) | Self-hosted **messaging gateway** |
| **Hermes Agent** (Nous Research) | Self-improving agent with persistent cross-session memory; auto-writes reusable skills. | ~60k in <2 months | Self-hosted **learning agent** |
| **Ruflo** (ruvnet, ex–"Claude Flow") | Meta-harness/swarm wrapping Claude Code & Codex with ~100 specialized coding agents. | ~40k–63k | **Coding swarm** layer |

**Key insight: none of these is Chad's shape.** OpenClaw/Hermes are headless servers you
talk to through chat apps. Ruflo is a coding meta-harness. **Chad is a desktop GUI app
with computer-use, voice, and local models.** That is both the opportunity (uncontested
category) and the risk (nobody is searching for it yet).

### What users LOVE about these agents (we must match)
- **Multi-channel reach** — talk to it from apps you already use (OpenClaw's core magic).
- **Sovereignty** — "all data on my machine, no telemetry, no lock-in" (Hermes's pitch).
- **Self-learning skills** that accumulate over time.
- **Proactivity** — deterministic cron/scheduling users can trust.
- **Persona/identity** — agents with names and character.
- **Absurdly low barrier to entry** — one-line install, a demo video showing real value.
  This is the single biggest predictor of viral stars in 2026.

### What users HATE (our opening)
**OpenClaw — the pain is setup + security, not the AI:**
- Self-hosting is a *nightmare* — Docker/SSH/hardening/uptime. "More time on infra than workflows."
- **Security catastrophe:** binds `0.0.0.0:18789` with **no auth by default**; ~135k exposed
  instances on Shodan; leaked Anthropic/Slack/Telegram creds; **60+ CVEs** incl. one-click RCE
  (CVE-2026-25253); a cloud DB leak exposed **1.5M API tokens**.
- **Supply-chain rot:** 341→824+ **malicious skills** on the community "ClawHub"
  (keyloggers, credential stealers, typo-squatted names).
- Updates break things (~25% of releases); memory cross-contaminates between projects.

**Hermes — the pain is trust:** self-evaluation is broken ("always thinks it did a good
job"), overwrites manual edits, immature (11 releases), suspected astroturfing.

**Universal:** compound failure. 85%-reliable steps over a 10-step workflow ≈ 20% end-to-end.
Integrations break silently. "Perfect in demos, fails in production."

---

## 2. Where Chad wins and loses (code-grounded)

Verified by reading the codebase, not assumed.

### Real strengths
1. **Small-local-model thesis is real.** Live adapter is `LiteLLMAdapter` (100+ providers);
   `LocalModelDiscovery` already enumerates LM Studio **and** Ollama. Matthew's actual
   runtime (`config/preferences.yaml`, gitignored) runs **all-local gemma-4-26b + qwen3.5-9b**
   and it works well. The heavy orchestration is done by small local models, not a paid API.
2. **Agent performance / orchestration** — supervisor-led multi-agent, semantic tool RAG
   (nomic embeddings, local), skills. This is our best feature.
3. **One-click `.exe` installer** — we already solved the #1 thing people hate about
   OpenClaw/Hermes (Docker/SSH hell). Most rivals can't be installed by a non-developer.
4. **Native computer-use + desktop GUI** — mouse/keyboard/screen/OS control. Structurally
   impossible for a messaging-gateway agent. This is the demo wow-factor.
5. **Local-only mode already shipped** — `src/infrastructure/database/local_store.py`
   (sqlite + numpy cosine; #416 COMPLETED). Memory/chat/tool-RAG work with zero cloud.
6. **We already have an eval harness** (`evals/`) — reliability is the field's #1 unsolved
   problem and we can *measure* it. Almost nobody advertises this.

### Real weaknesses / gaps
1. **Windows-only.** Rivals are cross-platform + mobile. Caps the star ceiling. (macOS Limb
   is the eventual 100x lever — but *after* the thesis is proven on Windows.)
2. **The public repo ships the wrong defaults.** Committed `config/config.yaml` still points
   at cloud `gemini-2.0-flash` for orchestration roles; the all-local gemma-4 setup lives only
   in the gitignored `preferences.yaml`. **A stranger who clones gets the cloud experience —
   the entire thesis is invisible out of the box.** (Epic: Local-First by Default.)
3. *(Historical — resolved by the Local-First by Default epic, #403/#416/#462/#465/#589;
   see `docs/setup/LOCAL_FIRST.md` for current state.)* **Local mode is built but not the
   default,** and several features stay Supabase-only
   (Telegram, kanban, workflows, dashboard, OATS, reflection, cron). Sovereignty story is
   only half-true until local is the default path.
4. **Not code-signed** (SmartScreen scare — #430 tracks this).
5. **Skills are format-heavy** (docx/pdf/pptx/…), light on *agentic-capability* skills and
   the self-improving loop that Hermes markets.
6. **No demo, no name recognition, zero stars.** Cold start.

---

## 3. Honest value rating

- Engineering: **8/10** (multi-agent, local tool-RAG, computer-use, eval harness — ambitious and real).
- Packaged star-getting product **today**: **4/10** (Windows-only, cloud-default out of the box,
  unsigned, no demo, no channel reach *enabled*, unknown-to-the-world).
- Blended honest value **today: 6/10**. Realistic star outcome in current form: **low hundreds.**

**With the Tier-0/1 epics below** (local-first default + prove the thesis + own the security
story + revive reach + one great demo): a credible **8/10 and low-thousands of stars**, with
computer-use as the breakout lever.

---

## 4. The strategy in one paragraph

Flip the defaults so a fresh clone runs **all-local by default** and the small-model thesis is
visible immediately. **Prove the thesis with numbers** — benchmark our harness+9B/26B against
frontier APIs on established agentic benchmarks, then ship our own personal-agentic benchmark
as a moat. **Own the security high ground** OpenClaw handed us ("the personal AI that can't be
Shodan'd"). **Re-enable the reach we already built** (Telegram + PWA). **Cut one 60-second demo**
of Chad doing a real multi-step desktop+web task on a local model. Then, and only then, go
cross-platform.

---

## 5. Epic map

New epics created for this initiative are listed in the master tracker (#219). Each is written
to be handed to a **cold agent session standalone**. Existing work we build on is referenced,
not duplicated.

| Epic | Tier | Status of prior art | One-liner |
|---|---|---|---|
| **Local-First by Default** (#448) | 0 | `local_store.py` shipped (#416); flip defaults + extend + document | Fresh clone runs all-local; Supabase is opt-in |
| **Prove the Thesis / Benchmarking** (#449) | 0 | builds on eval upgrade (#305) | Score harness+small-model on GAIA/τ-bench/BFCL, then ship ChadBench |
| **Model Provider Freedom** (#450) | 1 | LiteLLM + discovery exist; decouple lifecycle | First-class Ollama/llama.cpp/vLLM; no LM-Studio lock-in |
| **Reach: Telegram + PWA** (#451) | 1 | Telegram code exists; PWA shipped (#187), tunnel (#179) | Re-enable + productize the channels we already built |
| **Launch Story: Security + Demo** (#452) | 1 | code-signing (#430), prompt-injection (#341) | "Can't be Shodan'd" doc + signed installer + demo video |
| **Depth: Skills 2.0 + Self-Improve** (#453) | 2 | reflection (#360), triage-skill (#361) | More agentic skills + signed/sandboxed format + finish learning loop |
| _Computer-use flagship_ | 2 | **already tracked in #274** (+#276/#277/#278/#280) | Reliable safeguarded desktop automation — our structural moat |
| _One-command dev setup_ | 3 | **already tracked in #218 / #428** | Replace 3-terminal dance |
| _Voice hardening_ | 3 | voice epic #291 (closed) | Test it or mark experimental — don't let it tank first impressions |

---

## 6. Parallel execution playbook

Designed so you can hand each epic to a separate agent with **minimal file collisions**.

### File-ownership map (why the waves are safe)
| Epic | Primary files it touches |
|---|---|
| Local-First by Default | `config/config.yaml`, `config_manager.py`, `local_store.py`, `checkpointing.py`, `langgraph_engine.py`, `semantic_selector.py`, memory repos |
| Benchmarking | `evals/**` only |
| Model Provider Freedom | `adapters/**`, `model_lifecycle.py`, `model_swap.py`, `local_model_discovery.py`, `config/*model*` |
| Reach (Telegram + PWA) | `src/remote/telegram/**`, `src/clients/web/**` |
| Launch Story | `docs/**`, `packaging/**`, `.github/workflows/**` |
| Skills 2.0 | `src/skills/**`, `reflection*.py` |

**Only real collision: Local-First ↔ Model Provider Freedom** (both touch config + model layer).
Never run those two concurrently.

### Wave plan
**Wave 1 — run up to 4–5 agents in parallel (no collisions):**
- **#448 Local-First by Default** (owns config/db/orchestration)
- **#449 Benchmarking** (owns `evals/`)
- **#451 Reach: Telegram + PWA** (owns remote/telegram + web)
- **#452 Launch Story: Security + Demo** (owns docs/packaging/CI)
- **#453 Skills 2.0** (owns `src/skills/`) — light overlap with reflection storage; keep last if only 4 agents

**Wave 2 — after #448 lands its config/model changes:**
- **#450 Model Provider Freedom** (owns model layer; would collide with #448's config edits)

### Recommended realistic cadence (one human, don't overload yourself)
Run **2–3 agents at a time**, in this priority order:
1. **#448 Local-First by Default** (Tier 0, unblocks the thesis and is the biggest collider — do it first/solo-ish)
2. In parallel with #448: **#449 Benchmarking** + **#452 Launch Story** (fully isolated, both feed the launch narrative)
3. After #448 merges: **#450 Model Provider Freedom** + **#451 Reach** + **#453 Skills 2.0**

### How to run each agent
- Each epic issue is self-contained: problem, scope, file paths, acceptance criteria, and a
  "parallel-safety" note. Hand the issue URL to a fresh session.
- Use a separate git worktree per agent (`.claude/worktrees/…`) so branches don't stomp.
- Reference the epic in commits/PRs (`Refs #<epic>`).
- Merge order: Local-First first, then everything else; Model Provider Freedom last of the model-layer work.

---

## 7. "Ready for stars" launch checklist
- [ ] Fresh `git clone` → one command → all-local Chad running, no cloud keys (Local-First + #428).
- [ ] Committed defaults are local (gemma-4/qwen3.5-9b), not cloud Gemini.
- [ ] A published benchmark number: "harness + local model scores X on GAIA/BFCL vs frontier API."
- [ ] Signed installer, no SmartScreen scare (#430).
- [ ] "Why Chad can't leak like OpenClaw" security page + no-open-ports audit.
- [ ] One 60-second demo video: voice/text → real multi-step desktop+web task on a local model.
- [ ] Telegram + PWA re-enabled and documented.
- [ ] README rewritten to lead with the thesis, not the architecture diagram.

---

## Sources
- OpenClaw: <https://github.com/openclaw/openclaw> · Kaspersky <https://www.kaspersky.com/blog/openclaw-vulnerabilities-exposed/55263/> · ToxSec (Shodan/defaults) <https://www.toxsec.com/p/openclaw-is-a-wildly-insecure> · Hidden Layer <https://www.hiddenlayer.com/research/exploring-the-security-risks-of-ai-assistants-like-openclaw>
- Hermes Agent: <https://github.com/nousresearch/hermes-agent> · OpenClaw vs Hermes (1,300 Reddit comments) <https://kilo.ai/openclaw/vs-hermes>
- Ruflo: <https://github.com/ruvnet/ruflo>
- Field: Forbes "why most AI agents fail" <https://www.forbes.com/councils/forbesbusinesscouncil/2026/02/12/why-most-ai-agents-fail-at-real-world-workflows/> · Vellum best OSS assistants <https://www.vellum.ai/blog/best-open-source-personal-ai-assistants> · Firecrawl "what makes repos go viral" <https://www.firecrawl.dev/blog/best-github-repos>
- Benchmarks: Agentic leaderboard <https://awesomeagents.ai/leaderboards/agentic-ai-benchmarks-leaderboard/> · BFCL v3 <https://pricepertoken.com/leaderboards/benchmark/bfcl-v3> · tau/BFCL guide <https://www.spheron.network/blog/tool-calling-benchmarks-bfcl-tau-bench-latency-optimization/>
- Local persistence: langgraph-checkpoint-postgres <https://pypi.org/project/langgraph-checkpoint-postgres/> · pgvector+LangGraph <https://medium.com/@sajith_k/using-postgresql-with-langgraph-for-state-management-and-vector-storage-df4ca9d9b89e>
