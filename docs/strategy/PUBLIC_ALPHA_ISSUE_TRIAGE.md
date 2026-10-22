# Public alpha issue triage proposal

Prepared October 21, 2026. No issues have been edited or closed by this release task.

Inventory: 51 open issues; one open PR (#463) reviewed separately. Classification does not establish that an issue is fixed.

## Release verification / safety

- [#219: 🚀 Open Source Readiness — Master Tracking Issue](https://github.com/MatthewLoyed/AIAssistant/issues/219) — Rewrite stale checkbox state around the bounded alpha gates; preserve completed work references.
- [#341: evals: prompt-injection-via-tool-results dataset + harness support](https://github.com/MatthewLoyed/AIAssistant/issues/341) — Check enabled tools against injected content and outbound approvals. Existing prompt guidance alone is not proof of resistance.
- [#409: Cold-start acceptance dry-run on a clean machine (integration test of setup)](https://github.com/MatthewLoyed/AIAssistant/issues/409) — Keep open until clean Windows acceptance is recorded. Source setup and installer acceptance are separate.
- [#412: Go-public process: dev integration branch + branch protection](https://github.com/MatthewLoyed/AIAssistant/issues/412) — dev/default-branch setup exists. Verify branch protection at public flip; no reason to redo integration setup.
- [#695: Memory: tier-1 consent bypassed on finances + 52-call manage_user_memory loop (11 min)](https://github.com/MatthewLoyed/AIAssistant/issues/695) — Candidate adds desktop approval for save/forget and prevents Telegram bypass. Do not claim the full live-loop issue resolved without replay/verification.
- [#713: Two tests fail on dev: broken tests.unit import + red cohesion ratchet](https://github.com/MatthewLoyed/AIAssistant/issues/713) — Candidate repairs outdated fixtures but retains the existing cohesion baseline. The structural guard still fails; this issue remains open. A previously reported all-green unit run used a relaxed baseline and is not release evidence.

## Scope-dependent alpha limitations

- [#235: Chat mode: investigate false multimodal override triggering 9B selector](https://github.com/MatthewLoyed/AIAssistant/issues/235) — Reproduce only if it affects the selected local model/chat path; otherwise document as a known limitation.
- [#413: Launch eval sweep — every tool type + complex workflows (judge = gemma)](https://github.com/MatthewLoyed/AIAssistant/issues/413) — Restrict release acceptance to advertised/default-enabled tasks. A sweep of every experimental capability is not required.
- [#700: code_agent produces no diff: reads and narrates instead of editing (0 tools in round 1, no-op after 3 nudges)](https://github.com/MatthewLoyed/AIAssistant/issues/700) — If coding is advertised, demonstrate a real edit/artifact and inspect the diff; narration is not completion.

## Reconcile implemented work; leave optional work separate

- [#430: Code signing + GitHub Actions release workflow for the installer](https://github.com/MatthewLoyed/AIAssistant/issues/430) — Release workflow exists and is active. Signing remains optional/unfunded; split that task rather than saying the entire workflow is missing.
- [#452: [Epic] Launch Story — security-as-identity + killer demo video](https://github.com/MatthewLoyed/AIAssistant/issues/452) — Security docs and README work exist. Update overly broad safety claims; demo remains optional for this alpha.

## Later work — does not block this Windows alpha

- [#274: Epic: Personalized computer use (browser DOM-first + Qwen3-VL desktop agent)](https://github.com/MatthewLoyed/AIAssistant/issues/274)
- [#277: Windows UIA accessibility-tree extraction + shared set-of-mark overlay generator](https://github.com/MatthewLoyed/AIAssistant/issues/277)
- [#278: computer_use_agent: desktop vision-in-loop (after Fara browser spike #288)](https://github.com/MatthewLoyed/AIAssistant/issues/278)
- [#280: computer_use eval lane: ScreenSpot-style grounding + live golden tasks](https://github.com/MatthewLoyed/AIAssistant/issues/280)
- [#286: Feat: Standalone Developer Dashboard & Branch Switcher](https://github.com/MatthewLoyed/AIAssistant/issues/286)
- [#294: Live voice: Phase 4 (stretch) — barge-in / interruption](https://github.com/MatthewLoyed/AIAssistant/issues/294)
- [#295: Live voice: Phase 5 (stretch) — full-duplex spike (Freeze-Omni)](https://github.com/MatthewLoyed/AIAssistant/issues/295)
- [#352: Integrate Walkr for scripted video walkthroughs (stretch)](https://github.com/MatthewLoyed/AIAssistant/issues/352)
- [#353: Local Flux image gen via ComfyUI (AMD ROCm / RX 9070 XT)](https://github.com/MatthewLoyed/AIAssistant/issues/353)
- [#355: [Epic] Chad DevOps — 24/7 harness regression operator (Use Case 1)](https://github.com/MatthewLoyed/AIAssistant/issues/355)
- [#356: evals: interactive data viz quality benchmark suite (Use Case 2)](https://github.com/MatthewLoyed/AIAssistant/issues/356)
- [#357: feat: dashboard HTML template library for personal data viz (Use Case 2)](https://github.com/MatthewLoyed/AIAssistant/issues/357)
- [#358: [Epic] Chad-on-Chad autonomous dev loop (Tier 3, long-term)](https://github.com/MatthewLoyed/AIAssistant/issues/358)
- [#359: feat: Cursor SDK bridge — Chad orchestrates cloud coding agents (Tier 3)](https://github.com/MatthewLoyed/AIAssistant/issues/359)
- [#360: feat: wire capability self-improvement sandbox verify (stage 6)](https://github.com/MatthewLoyed/AIAssistant/issues/360)
- [#396: Survey OSS tools to expand Chad's non-text capability surface (Manus-style)](https://github.com/MatthewLoyed/AIAssistant/issues/396)
- [#449: [Epic] Prove the Thesis — external benchmarks (GAIA/BFCL/τ-bench) + ChadBench](https://github.com/MatthewLoyed/AIAssistant/issues/449) — Published external benchmarks are optional for the alpha. Do not make unsupported comparative claims in the README.
- [#451: [Epic] Reach — re-enable Telegram + PWA phone control](https://github.com/MatthewLoyed/AIAssistant/issues/451)
- [#453: [Epic] Depth — Skills 2.0 + self-improving loop](https://github.com/MatthewLoyed/AIAssistant/issues/453)
- [#457: 449a: BFCL adapter + raw-local/frontier arms + BENCHMARKS.md skeleton](https://github.com/MatthewLoyed/AIAssistant/issues/457)
- [#458: 449b: Chad harness arm for BFCL (selector/binder bridge) + filled 3-way table](https://github.com/MatthewLoyed/AIAssistant/issues/458)
- [#459: 449c: GAIA Level-1 validation slice + methodology appendix](https://github.com/MatthewLoyed/AIAssistant/issues/459)
- [#460: 449d: ChadBench v0 — personal multi-intent dataset + lane flag](https://github.com/MatthewLoyed/AIAssistant/issues/460)
- [#461: 449e: tau-bench smoke footnote (optional)](https://github.com/MatthewLoyed/AIAssistant/issues/461)
- [#512: [Epic] Chad 10x performance initiative — parallelism, caching, coder reliability](https://github.com/MatthewLoyed/AIAssistant/issues/512)
- [#515: Plan-DAG tool orchestration (LLMCompiler pattern) — replace per-hop ReAct](https://github.com/MatthewLoyed/AIAssistant/issues/515)
- [#518: Role-orchestration: planner/executor/critic on one small model](https://github.com/MatthewLoyed/AIAssistant/issues/518)
- [#542: [Live test] #513 parallel sub-agent fan-out](https://github.com/MatthewLoyed/AIAssistant/issues/542)
- [#569: [Epic] Chad Code — OpenHands-backed 24/7 delegated coding worker](https://github.com/MatthewLoyed/AIAssistant/issues/569)
- [#575: Chad Code: unblock the app-server web-UI path (exposed_urls race)](https://github.com/MatthewLoyed/AIAssistant/issues/575)
- [#579: Chad Code: Chad-side orchestration — research → seed a coding task](https://github.com/MatthewLoyed/AIAssistant/issues/579)
- [#655: [Epic] Memory quality: temporal facts, typed runtime writes, and write-path learning](https://github.com/MatthewLoyed/AIAssistant/issues/655)
- [#662: Memory: rebalance the per-turn context budget — identity is outnumbered 11:6](https://github.com/MatthewLoyed/AIAssistant/issues/662)
- [#678: Voice mode: rolling voice-session model (short-term memory + inspectable history)](https://github.com/MatthewLoyed/AIAssistant/issues/678)
- [#697: 355 Phase 4: mine FAILURE traces into staged regression cases (judge drafts assertions)](https://github.com/MatthewLoyed/AIAssistant/issues/697)
- [#698: 355 Phase 5: recurrence detection - reopen the closed issue instead of filing a new one](https://github.com/MatthewLoyed/AIAssistant/issues/698)
- [#710: PWA: pair by scanning the QR from inside the installed app](https://github.com/MatthewLoyed/AIAssistant/issues/710)
- [#715: Design decision: is the Brain/Limb tool partition still earning its keep?](https://github.com/MatthewLoyed/AIAssistant/issues/715)
- [#716: PWA capability parity with the desktop GUI](https://github.com/MatthewLoyed/AIAssistant/issues/716)
- [#720: [Live Eval Audit] 3-Hour Autonomous Planning -> Execution Run (Matthew Personal Suite)](https://github.com/MatthewLoyed/AIAssistant/issues/720) — Review personalized eval content before visibility changes; an old IN PROGRESS status needs reconciliation, not an automatic rerun.

## Pull request #463

BFCL benchmark adapter targets master. Compare its commits with current dev and the benchmark epic before retargeting, closing, or including it. Benchmark work is not a launch gate. Do not merge it directly into master during release preparation.

## Cleanup policy

Update implemented portions with evidence; close only demonstrably completed or duplicate items. Keep unimplemented ideas in the backlog without promised delivery dates. Privacy review may require redacting existing issue/comment content; propose exact redactions privately before applying them.
