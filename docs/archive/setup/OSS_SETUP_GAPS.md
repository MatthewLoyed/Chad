# Open-Source Setup Gap Audit

_Last updated: 2026-07-11. Companion to master tracker [#219](https://github.com/MatthewLoyed/AIAssistant/issues/219)._

This doc lists **implemented features** whose newcomer/setup story is incomplete for
a public clone. It complements `docs/strategy/OPEN_SOURCE_STRATEGY.md` (positioning)
and `docs/setup/COLD_START.md` (launch gate).

**Legend**

| Status | Meaning |
|---|---|
| ✅ Ready | Documented in `docs/setup/` and verifiable on a clean machine |
| 🟡 Partial | Code works; docs live only under `docs/features/` or `secrets.yaml.example` |
| 🔴 Gap | Missing or misleading newcomer path; may surprise OSS visitors |
| ⬜ Advanced | Intentionally optional / Supabase-heavy; OK to stay post-launch |

---

## Blockers (required before public flip)

Tracked as dedicated issues; must pass #409 dry-run.

| Feature | Status | Issue | Notes |
|---|---|---|---|
| Personal knowledge graph | 🟡 → ✅ docs added | [#492](https://github.com/MatthewLoyed/AIAssistant/issues/492) | `PERSONAL_KNOWLEDGE.md`; verify User Facts on clean machine |
| Browser / activity history | 🟡 → ✅ docs + opt-in fix | [#493](https://github.com/MatthewLoyed/AIAssistant/issues/493) | `BROWSER_HISTORY.md`; `daemons.ingestion_sync` now defaults **off**, opt-in via Settings → Capabilities (was a silent-by-default privacy gap); `system_state` local shim registered so watermarks persist |

---

## Tier 1 — Enabled by default, weak newcomer story

These ship **on** in `config/config.yaml` or daemon defaults. A stranger hits them
without opting in.

| Feature | Default | Setup doc | Gap |
|---|---|---|---|
| **E2B code sandbox** (`code_agent`) | On (README) | None in `docs/setup/` | README says **On** but only `E2B_API_KEY` in `secrets.yaml.example`. No guide for getting a key, what happens without it, or local alternatives. First coding prompt may fail cryptically. |
| **Heartbeat daemon** | `daemons.heartbeat: true` | `docs/features/HEARTBEAT_USAGE_GUIDE.md` only | Runs every 30 min with no newcomer explanation. `docs/DASHBOARD.md` is a stale static template, not a setup guide. Users don't know they can configure checks via `manage_heartbeat` or Settings → Automations. |
| **Cron daemon** | `daemons.cron: true` | Buried in `TELEGRAM_SETUP.md` / webhook guide | No `docs/setup/` entry for scheduled tasks independent of Telegram. |
| ~~Ingestion sync (Chrome/Gmail/Drive)~~ | `daemons.ingestion_sync: false` (opt-in, Settings → Capabilities) | ✅ `BROWSER_HISTORY.md` (2026-07-11) | Fixed — no longer on by default. Kept here struck through for history; Gmail/Drive legs still depend on [CONNECTORS.md](CONNECTORS.md); Chrome is Windows-only. |
| **Reflection** (`reflection.enabled: true`) | On | `docs/features/CAPABILITY_SELF_IMPROVEMENT.md` (related) | Propose-only self-learning runs on heartbeat sub-cycle. GUI Reflection panel exists but no cold-start mention. Local persistence works (#467) — COLD_START still says "Supabase-only" (stale). |
| **Memory consolidation** | `consolidation.enabled: true` | None | Background session summarization with no newcomer doc. |
| **Skills catalog** | Auto-loaded from `src/skills/` | `docs/architecture/skill-system.md` (contributor) | No user-facing "what skills exist / how to add your own SKILL.md" in setup path. |

**Recommended follow-ups (post-#492/#493, pre- or post-public):**

1. `docs/setup/CODE_SANDBOX.md` — E2B key, failure modes, disable `code_agent` if no key
2. `docs/setup/BACKGROUND_AUTOMATION.md` — heartbeat, cron, daemons toggle, Automations UI
3. Update COLD_START "Supabase-only" list — reflection lessons + dashboard now have local shims (#467, #469)

---

## Tier 2 — Opt-in integrations without setup guides

Code + tools exist; credentials in `secrets.yaml.example` or hardcoded paths; no
`docs/setup/` guide.

| Feature | Credential / config | Setup doc | Gap |
|---|---|---|---|
| **Canvas LMS** (10+ tools) | `CANVAS_BASE_URL` + `CANVAS_TOKEN` in `secrets.yaml.example` | [CONNECTORS.md](CONNECTORS.md) | ✅ Native connector wizard (2026-07-11) — was hardcoded `msstate.edu` |
| **Hubspace smart home** | `HUBSPACE_*` in secrets | None | `hubspace_control` tool; referenced in voice lane config comment only. |
| **E2B sandbox** | `E2B_API_KEY` | None | See Tier 1. |
| **Voice / wake word** | None (openWakeWord — free, local, no account) | `docs/features/VOICE_MODE.md` | Off by default — 🟡 partial (feature doc OK; add `docs/setup/VOICE.md` pointer from COLD_START). |
| **Presenton decks** | Docker + `PRESENTON_*` | `docs/features/PRESENTON.md` | Optional sidecar — 🟡 partial (cold-start Docker section covers tier). |
| **LangSmith tracing** | `.env` keys | Mentioned in README | 🟡 partial. |

---

## Tier 3 — Advanced / intentionally deferred

Documented in COLD_START as optional or Supabase-oriented. Fine for post-launch
if labeled clearly in README "experimental / advanced."

| Feature | Local support | Notes |
|---|---|---|
| Telegram | Native connector | ✅ `TELEGRAM_SETUP.md` |
| Gmail webhooks | `WEBHOOK_SETUP_GUIDE.md` | Cloud Brain + HTTPS only |
| Kanban / workflows | LocalPostgrestShim (#462) | No user setup doc; power-user feature |
| Dashboard feed | Local shim (#469) | Chad-authored feed; no newcomer doc |
| Capability self-improvement | Gap log appears Supabase-oriented | Feature doc exists; queue is advanced |
| OATS vector refinement | Supabase RPC | Eval/internal; not user-facing |
| PWA / phone client | #451 epic | Experimental per #219 friends launch |
| Browser automation (`browser_agent`) | Off by default | ✅ `BROWSER_AUTOMATION.md` |
| Computer-use / UI automation | Off by default | ✅ SECURITY_POSTURE + feature docs |

---

## Code hygiene gaps (not setup docs, but OSS blockers)

| Item | Location | Risk |
|---|---|---|
| ~~Canvas base URL + User-Agent~~ | ~~`src/tools/canvas/manager.py`~~ | Fixed 2026-07-11 — configurable via Connectors wizard |
| `CANVAS_*` in secrets template | `config/secrets.yaml.example` | ✅ Added |
| COLD_START stale "Supabase-only" list | `docs/setup/COLD_START.md` L158–160 | Reflection + dashboard now local-capable |
| `StateRepository` / `system_state` | `src/infrastructure/repositories/state_repository.py` | Browser-history watermarks don't persist local-only (#493 tech debt) |

---

## Mapping to #219 phases

| #219 phase | This audit |
|---|---|
| Phase 2 cold-start (#409) | #492, #493 + Tier 1 E2B/heartbeat gaps for a honest dry-run |
| Phase 3 README (#410) | Reconcile "enabled by default" table with Tier 1 (E2B especially) |
| Post-launch | Tier 3 + skills contributor story |

---

## How to extend this audit

When adding a feature with external credentials or background daemons:

1. Add a row here.
2. Add `docs/setup/<FEATURE>.md` or a section in COLD_START.
3. Add credential placeholders to `secrets.yaml.example` / CONNECTORS wizard.
4. If enabled by default, document the **no-credentials failure mode**.
