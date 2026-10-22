# Proof-of-concept trajectories (2026-07-03)

Curated runs from the hybrid Gemma + DOM probe and one Fara contrast case.
Full writeup: [`docs/features/BROWSER_HYBRID_PROBE.md`](../../../docs/features/BROWSER_HYBRID_PROBE.md).

Model unless noted: `google/gemma-4-26b-a4b-qat` via `evals/browser_hybrid_probe.py`.

| File | Task | Outcome |
|---|---|---|
| `hybrid_hn_pass.json` | HN #1 title + points | **Pass** — title matched live page |
| `hybrid_autotrader_ca_pass.json` | AutoTrader.ca Mazda listing year/model/price | **Pass** (~4 turns) |
| `hybrid_gta6_people_pass.json` | GTA VI character names on rockstargames.com only | **Pass** — Jason, Lucia |
| `hybrid_smallbiz_owners_pass.json` | example-smallbiz.com owners (About) | **Pass** — Jordan Sample, Alex Example |
| `hybrid_smallbiz_signup_partial.json` | Early-access form (Chad Smith / email / Huntsville / Club) | **Partial** — name/email/city filled; membership widget + submit not finished |
| `fara_autotrader_max_turns.json` | Fara-7B thin probe, Mazda filters + listing URL | **Fail** — reached a US listing URL, no terminate (contrast) |

Transient logs and other runs stay gitignored under `evals/runs/`. Only this `proof/` folder is tracked.
