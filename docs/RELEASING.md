# Releasing Chad

This guide owns release evidence, approval points and the publication procedure.

## Current release pipeline

The public repository is `MatthewLoyed/Chad`. Feature PRs merge into `dev`, then
an approved promotion PR merges `dev` into `master`. A source merge makes code
public; it does **not** update the previously published Windows installer.

1. A runtime change promoted to `master` runs **Release installer**. The workflow
   can also be dispatched with an explicit version (for example `0.2.0-alpha.11`);
   otherwise it chooses the next alpha after existing releases, including drafts.
   Documentation-only/download-link promotions do not rebuild the installer.
   Tag pushes do not trigger builds: publishing a draft creates its tag, and
   rebuilding on that event would replace the accepted binary. Existing release
   versions (including drafts) are reserved; retries must choose a new version.
2. The workflow builds GUI + Brain from that exact commit, tests silent install
   and reinstall/data retention in an isolated runner directory, and creates an
   **unpublished draft** with the installer, `SHA256SUMS`, `release-manifest.json`
   and `installer-smoke.json`. Missing dependencies or assets fail the build.
3. Download the exact draft asset and verify its SHA-256 against the manifest.
   Check it on the clean Windows acceptance VM: startup, model connection, chat,
   persisted state, Feed layout, and upgrade retention. Record actual evidence
   and limitations in the draft release notes. Packaging smoke alone is not UI
   or model acceptance. Never run these checks against the production Brain.
   Use a dedicated model server for acceptance, rather than the maintainer's
   active LM Studio server. Record resident instances before and after each
   model test; unexpected model choices, duplicate instances or memory exhaustion
   block publication and further model tests until investigated.
4. Publish only the accepted draft. The **Update installer download after
   publication** workflow verifies that the installer asset exists, then opens a
   README download-link PR into `dev`. Promote that link change through `master`
   after CI passes. The workflow requires Actions to be allowed to create PRs;
   if disabled, use the same `release_metadata.py readme --tag <tag>` command and
   open the PR normally. Do not silently broaden token permissions.
5. Verify the public release/README download without credentials and compare the
   downloaded bytes with the accepted checksum. Installer users manually run the
   new installer; the in-app Git update command is for source checkouts.

The draft gate is intentional: tags and builds do not establish acceptance, and
publishing a release must not expose an untested installer. A failed release
build leaves the existing download intact. Release maintenance owns this guide,
`.github/workflows/release.yml`, `.github/workflows/release-download.yml` and the
helpers in `scripts/prod/release_metadata.py` and `test_installer.ps1`.

The October 22 records below describe the original publication and superseded
infrastructure; they are historical evidence, not the current procedure.

### Alpha.13 artifact repair — October 27, 2026

Publishing alpha.13 triggered the former tag-push build, which replaced its
accepted installer and provenance files after publication. The replacement had
SHA-256 `fd243186f5b674f7965930b160a0f86ff162322242e337f57bb1e59c961e355c`
(391,662,788 bytes); the release notes and acceptance evidence describe the
original SHA-256 `b0a77f5273a1501901e0d6a726c27040e7783c11e2c04ad241226496596e0491`
(391,664,022 bytes). Restore only the retained, hash-verified accepted installer
and its original manifest/checksums/smoke evidence; do not relabel acceptance
reports to certify the replacement. The workflow now creates new drafts only
and rejects existing release versions before upload.

## Clean repository publication — October 22, 2026

The original publication destination was `MatthewLoyed/Chad`, then private, with a fresh source
snapshot and no imported legacy Git history, discussions or old releases.
The original development repository remains private. Legacy issue/commit links
below are historical maintainer references and are unavailable to public readers;
report new bugs in this repository's Issues tab.

The release promise is Windows **Chat**: setup, conversation, summaries, rewrites,
Stop and persisted history. The already tested unsigned alpha.9 installer is
unchanged. Repository-only cleanup affects documentation, CI and developer helper
paths; it does not rebuild or alter that binary. Its hash and VM evidence below
remain the acceptance authority. No video or additional features are required.

### Updates

Installer upgrades are manual: download the next installer from this repository's
Releases tab and run it. Automatic updates default off. The existing in-app
Update action fetches `origin/master` from a source checkout; the packaged payload
contains no `.git` directory and this action does not upgrade an installed alpha.
There is no embedded legacy repository remote to migrate in that installer.
Do not advertise automatic packaged updates. Source contributors clone this
repository; its `master` branch is created only during approved promotion.

### CI and publication settings

Documentation, web build, unit tests and offline harness checks use read-only
Actions tokens and bounded job times. CI receives no model/Supabase credentials.
Secret scanning uses Gitleaks plus TruffleHog without sending candidate values
to providers. Only three exact detector/path/value-hash combinations are reviewed
public placeholders: an Electron dependency commit and two example Postgres URLs.
New candidates, including different values in those files, fail the check.

The tag-triggered installer rebuild stays disabled for the initial publication:
publish the exact accepted binary, not an untested replacement. Prepare an
unpublished draft and verify GitHub's stored asset digest against the local hash.
Draft creation must not create a tag; verify that before asking for publication.

GitHub currently returns 403 for private-repository protection and 404 for private
vulnerability reporting on this account's plan. These settings must be applied
and re-read during the approved public transition, without purchasing an upgrade:

1. Import the reviewed clean snapshot into the final private repository; establish
   `master` from approved source at publication and keep `dev` for integration.
2. Change visibility only after the source/history privacy review is accepted.
3. Protect `dev` and `master`: require PRs and the passing `docs`, `gitleaks`,
   `trufflehog`, `build` and `evals` checks; block force pushes and deletion;
   require resolved conversations. A solo maintainer need not approve their own PR.
4. Enable private vulnerability reporting and verify the SECURITY.md report URL;
   enable available free secret-scanning/push-protection features.
5. Point the draft release at approved `master`, then publish its alpha prerelease
   and tag only with approval. Keep the automatic installer rebuild disabled.
6. Verify unauthenticated clone, README navigation, security report availability,
   release download and installer SHA256. Those public checks cannot pass while
   the repository/draft remain private; do not claim they were already run.

The initial private staging repository is retained under the separate name
`Chad-private-release-preparation`, including its earlier path-bearing commits
and CI logs. The final `Chad` repository starts from the reviewed clean snapshot
in a separate repository identity, without those refs or logs. A verified private
Git bundle is retained too. This preserves both preparation and original
development history privately; neither repository is rewritten or made public.

> **Historical legacy-repository state, October 21, 2026:** the secret scan, free harness evals, and web
> build workflows are manually disabled. The installer release workflow is
> active; this does not establish that Actions minutes are available or that a
> new build will pass. Use the local gates while private, then restore and verify
> the workflows at publication. Get explicit approval before promoting to master.
> Tags create a **draft** release; publish it only after verifying its artifact.
> Tags containing a hyphen (for example `v0.2.0-alpha.1`) are prereleases.
> The branch-protection API currently returns HTTP 403 for both dev and master:
> GitHub requires a plan upgrade or public visibility for this repository's
> protection feature. Do not buy an upgrade for this alpha; recheck and configure
> protection as part of the approved public transition.

## Experimental Windows alpha preparation â€” October 22, 2026

The [bounded release plan](strategy/PUBLIC_ALPHA_RELEASE_PLAN.md) defines the
promise and approval points. The [issue triage proposal](strategy/PUBLIC_ALPHA_ISSUE_TRIAGE.md)
covers the 51 open issues. Release maintenance owns this evidence.

The candidate is on `codex/public-alpha-release`, in draft PR #722 into dev.
Draft PR #723 prepares dev-to-master promotion. Neither is approved to merge.
The original working folder and an all-ref Git bundle are preserved privately.
Master is the existing in-app update channel; promotion can affect existing installs.

Current runtime source: `50a46a65`, built as private unsigned alpha.9. It includes:

- Native desktop detection that waits for pywebview injection instead of
  permanently falling back to the browser connection form during a cold start.
- Early multiprocessing worker diversion in both frozen entrypoints. Starting
  another Brain no longer kills the process already using its port.
- First-run setup updates both active and supervisor model aliases, reloads
  routing, and recognizes a saved custom backend after restart.
- Exact desktop consent for memory save/forget; Telegram cannot approve writes.
  Legacy learn directives cannot bypass consent. Proactive extraction proposes
  facts without ingesting them; failure reflection is opt-in and review-only.
- Optional inference queuing is off: its source-only launcher recursively
  re-enters a frozen Brain. Chat uses the selected backend directly.
- Shipped persona/contact examples are anonymized. Generated personal caches
  and untracked overlays are excluded from installer staging.
- Voice, autonomous daemons and the newer experimental browser controller default
  off. Developer tools follow `dev_debug`; login startup is optional and off.

Local verification:

- Python unit tests: **2,601 passed, two skipped, zero failed**; 39 subtests passed.
  The maintainer explicitly approved the one-time code-organization exception
  recorded in MODULE_COHESION.md and commit `3c9546f6`: 124 current entries are
  frozen, the original thresholds/ratchet tests remain active, and future growth
  must still be fixed. The full clean-config run passed after that approval.
  Test-only corrections isolate mocked provider HTTP tests from ambient eval
  restrictions and assert that unapproved learn directives never ingest facts.
- Frontend: **309 tests in 59 files passed**, production build passed, lint zero
  errors/25 warnings.
- Tool contract: **153 native tools passed**, with the original grandfathered
  formatter/UI baseline unchanged. Prompt audit: **9/9 passed** against real
  local fragments; budget warnings remain. Missing local assets fail closed.
- No tool-selection suite or paid/live model evaluation was dispatched. Replay
  requires a Supabase holdout database; it is not an offline installation check.
- The morning current candidate/master/dev ancestry scan found zero credential
  matches (704 commits at `0dc4a581`). Final-candidate scanning must be refreshed after the
  remaining documentation commits. This does not clear all historical refs.

Artifact and VM acceptance:

**Final alpha.9 acceptance passed October 22.** The unsigned installer is
446,344,322 bytes; SHA-256
`C19F5A5291C9D5E9B3E06D0EB9669FB90427288090C8BB81E2A668C2F0AD1D64`.
Both application hashes and all 33 staged web assets match the final build.
Its 342 scanner findings are in 103 byte-identical installed dependency files,
with none unmatched. Installation exited zero without elevation, startup stayed
off, OAuth files were absent and the synthetic user-data marker survived.

Using the installed desktop, the visible first-run wizard discovered and saved
the custom guest model with the API-key field blank. Chat completed a greeting,
a correct Monday-10-AM/notebook workshop summary and a polite rewrite. The Stop
button cancelled a longer story with `user_cancelled`. Restarting both guest
applications retained the selected model, conversation and cancelled partial;
exactly one Brain process listened afterward. No backend setup command or model
selection workaround was used in this final pass. Earlier failed builds below
are diagnostic history, not the final result.

The model chip can retain its initial cached label until desktop restart after
setup, although routing uses the chosen backend. Optional connector tiles are
not service-readiness checks. Agent/tool-selector acceptance is limited by a
missing Torch runtime dependency in the guest; no Agent/GPU/browser/voice claim
is made. The guest model backend required its own runtime dependencies.

Alpha.6 built from `4f0fea07` and installed without elevation with matching
hashes, runtime assets, no OAuth files, startup off and retained synthetic data.
Its actual desktop test caught a WebView2 local-file query bug. `b10331d6`
preserves file URLs. Alpha.7 component testing then completed the visible custom
backend wizard, including discovery/save of the guest model, but Chat failed
because bootstrapping the active placeholder Supabase credentials in `.env.example`
enabled an unusable cloud-history backend. `d3a1f824` leaves optional credentials
commented out; a real SQLite conversation roundtrip regression test passes.

That corrected component test created local chat/history and displayed the
selected model after restart, then exposed an SDK requirement for a nonempty key
on unauthenticated OpenAI-compatible endpoints. `50a46a65` supplies a public
non-secret sentinel for static endpoints, preserves explicit keys, and leaves
cloud requests unchanged. Twenty-one targeted provider/packaging tests passed,
including actual captured request parameters. Both executables rebuilt successfully
for alpha.9. Do not count these component checks as final-installer acceptance.
The final alpha.9 acceptance and hashes above supersede those failures.
Earlier alpha.5 installed without elevation, matched both application hashes,
kept startup off, and retained preferences plus a synthetic data marker.
Alpha.3 uninstall removed binaries/registration while retaining user data.

A free isolated Windows Enterprise evaluation VM contains no personal accounts,
Node, Git, or development checkout. A separately downloaded llama.cpp b11799 CPU
backend and Qwen3-0.6B Q8 model matched their published checksums. Alpha.4 completed
three Chat-mode turns through backend setup commands: greeting, a correct summary
of a fictional workshop, and a polite rewrite. All ended with `task_completed`.
That is backend component evidence: the normal desktop wizard still failed on
that older build. Alpha.6 must verify the actual wizard-to-chat path and saved
model persistence. The small CPU test does not establish larger-model/GPU or
Agent/tool-selector performance. Missing optional MCP executables and model
runtime dependencies limit connector acceptance. Keep those claims out of the alpha.
The host production Brain has not been started, stopped or restarted.

Publication gates:

1. **Credential rotation confirmed by the user October 22** for the four old
   GitHub, Notion, Brave and Chad remote credentials. This is user confirmation,
   not provider testing. The completed reminder was removed.
2. The approved archive tag removal is complete with a verified private backup.
   A fresh mirror still contains 16 credential matches via 136 old PR refs,
   with no containing branches/tags. Rotation mitigates credentials; it does not
   erase historical copies or personal data. No history rewrite/purge is approved.
   Follow [GitHub's guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
3. Finish privacy remediation before public visibility. Scans covered 721
   issue/PR bodies and 664 comments with no Gitleaks credentials. Manual signal
   review found contact/date examples, local paths and a personal email requiring
   edits. All 37 exact private body edits were approved, applied and re-fetched;
   every body matched its approved draft. No discussion was deleted. A history search finds 37 commits changing the old contact
   identity; current-file edits alone do not remove those historical copies.
   Current-file anonymization does not clear Git history/discussion copies.
4. **Approved old-release retirement is complete October 22.**
   The obsolete GUI draft has a maintainer home pointer and lacks root runtime
   config/prompts. The old v0.1.0 installer was downloaded, digest-verified and
   extracted without execution; it predates the consent/startup fixes and lacks
   root prompts. Its 126 scanner matches are under third-party `_internal`
   files; compiled app privacy remains uncleared. Both obsolete releases and
   their downloads were removed with approval. Private backups and asset
   metadata remain intact; GitHub release list is empty. Source tags/history
   were preserved, including the existing v0.1.0 tag.
5. **Alpha.9 installer desktop acceptance is complete**, scoped to Chat and
   the demonstrated tasks above. The code-organization exception is approved
   and the full unit run is green. Do not disable checks or skip hooks.
6. Obtain explicit approval before candidate-to-dev merge, dev-to-master merge,
   public visibility, release tag, or publication. Restore required workflows
   and configure branch protection during the approved public transition.

Keep broad refactors, benchmarking epics, voice expansion and feature polish out
of this alpha. New ideas go to the backlog; fix only failures of the agreed promise.

## Branch model

```
feature/* â”€â”€PRâ”€â”€â–¶  dev  â”€â”€promoteâ”€â”€â–¶  master  â”€â”€tagâ”€â”€â–¶  vX.Y.Z (GitHub Release)
                    (integration)      (release channel)
```

- **`dev`** â€” integration branch. All feature/fix branches merge here via PR.
  This is where things are allowed to be briefly broken while a change settles.
- **`master`** â€” the **release channel**. Every deployed Chad follows it:
  - The in-app **Settings â†’ System â†’ "Update Chad"** button and the optional
    `updates.auto_update` poller both call `perform_self_update(branch="master")`
    (`src/remote/endpoints.py`), which pulls `origin/master`, rebuilds stale
    subsystems (web bundle, Brain, GUI), and gracefully restarts.
  - Therefore: **anything merged to `master` ships to real users on their next
    update.** Treat `master` as production. Never push to it directly.
- **Tags / GitHub Releases** â€” human-readable checkpoints on `master` for the
  changelog and installer artifacts.

## Branch protection (set once, in GitHub repo settings)

On both `master` and `dev`:
- Require a pull request before merging.
- Require status checks to pass (the `evals.yml` free lanes + web build/lint).
- On `master` additionally: restrict who can push, and do not allow force-push.

## Cutting a release

Follow the current release pipeline at the top of this guide. Stabilize `dev`,
run the relevant contribution checks, then promote through a reviewed PR into
`master`. Wait for required CI before merging. The master workflow builds a new
unpublished installer draft; verify the exact asset before publishing it. Do not
push a tag to request a rebuild or reuse an existing draft/published version.

Installer upgrades are manual downloads. The source-checkout Update command
requires Git and does not upgrade a packaged installation. Installers remain
unsigned until the signing secrets described in `scripts/prod/sign_installer.ps1`
are configured.

## Pre-release gate (before promoting dev to master)

Run the credential-free checks in [CONTRIBUTING.md](../CONTRIBUTING.md): unit
tests, prompt audit, tool contract and documentation integrity, plus the web
build/lint for frontend changes. CI also checks persona parity. Tool selection,
database replay and live model lanes are opt-in checks with their own prerequisites;
do not describe them as universally offline or token-free.

Source checks do not replace exact-installer acceptance. Verify cold startup,
the affected GUI/model paths, persisted state and upgrade retention on an isolated
acceptance installation before publishing. Use the production-port boundary and
model-residency rules in the current pipeline above.

## Hotfixes

For an urgent production fix, branch `fix/*` off `master` and open a PR into
`master`. After CI passes, use the same draft-build and exact-artifact acceptance
gates. Merge `master` back into `dev` so the source branches do not diverge.
