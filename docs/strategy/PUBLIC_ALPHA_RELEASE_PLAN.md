# Chad public Windows alpha release plan

Prepared October 21, 2026. This is a plan, not a claim that the gates below have passed.

## Agreed scope and approval points

- Experimental Windows alpha: installer, local model setup, chat, and a few demonstrated tasks.
- Browser may ship if it passes the bounded acceptance checks below. Do not defer it merely because it has new code. Voice can stay opt-in or be deferred.
- Audit, preserve, document, and commit legitimate current work. Generated captures and personal runtime data require separate treatment; do not blindly commit every file.
- Use a free local VM and a Windows evaluation image; no paid services.
- Preserve existing Git history where practical. Propose specific remediation if historical secrets or personal data are found.
- Prepare the promotion PR into master. Matthew approves before merging, changing visibility, or publishing a release. A release tag can trigger publication, so do not push it ahead of approval.

## Verified starting point

Git refs were fetched before comparison:

- Repository is private; GitHub default branch is dev.
- origin/dev is 236 commits ahead of origin/master, with zero master-only commits.
- Local chad-dev is 38 commits ahead and 7 behind origin/dev. It is distinct from origin/chad-dev.
- Current checkout has 43 modified tracked files and 37 untracked paths. Tracked diff alone is 533 insertions and 76 deletions; untracked product code is additional.
- Changes cover browser capture/control and scrolling; stream/progress UI; automation controls; Drive/auth/ingestion; prompts/routing; tests and benchmark scripts. These are uncommitted, not proven incomplete.
- Loose screenshots/browser snapshots and a modified tracked data/grounded_entities.json need privacy review.
- GitHub has 51 open issues and one open PR: #463, an older BFCL benchmark adapter PR targeting master.
- Private v0.1.0 already contains Chad-Setup-0.1.0.exe (~403 MB). A separate GUI release is a draft. Existing release assets are part of the public exposure review.
- Secret scan, Harness evals (free lanes), and Web UI build workflows are manually disabled. Release installer is active. Do not infer workflow health from the presence of YAML files.
- Host: Windows 11 Home, ~32 GB RAM, ~971 GB free on C:. Virtualization firmware is enabled and a hypervisor is present. No VM runner was discovered in the checked standard locations; compatibility still needs a trial.

## 1. Preserve and reconcile the release inputs

Agent can do:

- Inventory the local 38 commits, 7 dev-only commits, all modified/untracked files, and the main checkout without modifying the running production installation.
- Create recoverable local backups of current work before any integration. Keep backup locations private; do not publish raw snapshots that contain personal data.
- Review changes by purpose and existing tests; document what is implemented, verified, and still failing. Commit legitimate source/tests/docs in coherent batches. Leave generated or personal captures out of product commits, preserving them privately if needed.
- Use an isolated release branch/worktree based on current dev. Reconcile local work, fix conflicts, and run relevant regression checks. Avoid changing branches under this dirty checkout.
- Review #463 for relevance/duplication. Propose retargeting, inclusion, or closure with evidence rather than merging it automatically.

Done when: every intended product change has a known commit and test status, no work is discarded, and one candidate commit is selected. No force pushes or branch deletion are needed for routine integration.

## 2. Audit the full public exposure surface

Agent can do:

- Fix secret-scanner coverage: the current custom Gitleaks configuration has two custom rules but does not extend the built-in rules. Review broad allowlists too.
- Run redacted secret scans over the intended tracked tree, all refs/history that will be exposed, and the exact staged installer payload. Use a second scanner; record tool/config versions, scanned refs, and findings without copying secret values into reports.
- Manually review personal-data candidates: config defaults, persona prompts, eval fixtures/results, runtime JSON, machine paths, screenshots, URLs, emails, logs, and developer notes. Automated credential scanning is not a personal-data audit.
- Review GitHub issues, comments, PR bodies/discussions, release notes/assets, and accessible workflow logs/artifacts for personal content. Include closed historical discussions, not only open tickets.
- Specifically inspect personalized live-eval material such as #720 and memory reports such as #695. Treat these as review candidates, not proof that all their content must be removed.
- Check installer staging against tracked-safe inputs and verify no maintainer environment, token, account, browser profile, database, or absolute build-machine path is bundled.
- If a credential is real, identify the provider/account privately and coordinate revocation/rotation. Deleting a file alone does not invalidate a credential.

Matthew may need to do: provider-side credential rotation requiring login/MFA; choose remediation for sensitive historical content. History rewrites, destructive deletion, or force pushes require a specific reviewed proposal.

Done when: no unresolved real credentials or private content remain in the material to be exposed, and any compromised credentials are rotated. #404 being closed in July is not evidence that October's candidate is clean.

## 3. Reconcile issues and freeze release scope

Agent can do:

- Classify all 51 open issues into release blockers, known alpha limitations, later work, and implemented/duplicate/superseded work. Check closed launch tickets against current behavior too.
- Prepare exact proposed tracker edits/closures with commit or test evidence. Do not close an issue solely because it is old, and do not promise roadmap dates.
- Reconcile #219: its checkboxes still include work now present in code/docs. Split #430's implemented workflow work from optional paid signing. Reconcile #452's README/security work and remaining demo tasks.
- Verify #409 clean-start acceptance and #412 branch protection at publication time.
- Reproduce #713's failing-test reports against the candidate. Repair actual failures or explicitly identify obsolete reports; do not make checks green by weakening assertions.
- Triage #695 memory-consent/repetition behavior if memory writes are enabled, and #700 coding no-op behavior if coding is advertised. Fix, disable the affected capability, or narrow the release promise based on evidence.
- Give #341 injection coverage priority proportional to enabled tools. Check local network binding/auth, outbound action approvals, file boundaries, and bounded execution for the shipped configuration.
- Keep benchmarking epics, architecture redesigns, mobile parity, autonomous self-improvement, and voice expansion outside the alpha gate.

Done when: one small release checklist replaces the sprawling launch roadmap. Propose GitHub comments/notifications for approval before posting; this planning request does not require sending messages to other participants.

## 4. Verify and build the candidate

Agent can do:

- Run applicable Python unit/integration tests, deterministic harness gates, frontend typecheck/build/lint, and relevant frontend tests. Record candidate SHA and actual outcomes, including skipped/live-only checks.
- Fix regressions needed for the promised alpha; preserve deferred features behind explicit settings.
- Test browser, if included: launch with an isolated test profile, read/capture/scroll a long page, complete a harmless demonstrated task, handle stale page state, stop a turn, and recover from failure. No real messages, purchases, or account changes for acceptance.
- Build the installer from sanitized candidate inputs; inspect bundled content; record SHA-256 and build provenance. Do not relabel the old v0.1.0 binary as the new candidate.
- Check disabled workflows locally before restoring them and verify current Actions availability. Check which existing active workflows consume model credentials or money before dispatching them.

Done when: the built artifact corresponds to a specific tested commit and there are no unexplained failures affecting the release promise.

## 5. Clean Windows VM acceptance

Agent can do:

- Check current VirtualBox host/hypervisor compatibility, obtain official software and a Windows 11 Enterprise evaluation ISO, verify available checksums, and prepare a local VM. Start around 8 GB RAM, 4 vCPUs, and an 80 GB dynamically allocated disk; adjust after checking host load.
- Use NAT and avoid host folders/clipboard and personal accounts as defaults. Take a clean snapshot, transfer only the candidate installer, then follow the public instructions as a new user.
- Verify installation as a standard user; first-run wizard; model connection; first reply; a demonstrated task; stop/error/reconnect behavior; app/VM restart; persistence; and uninstall/reinstall behavior. Verify no development checkout, Python, Node, maintainer secrets, or cloud database is required by the installer path.
- Record setup friction, time to first successful reply, app logs without secrets, screenshots, and fixes. Repeat from the clean snapshot after relevant fixes.

Matthew may need to do: Windows/UAC consent, interactive account/license/registration screens, and steps unavailable through the current tools. Native desktop automation is disabled in this session; use VM CLI/guest scripting where available and guide the remaining GUI steps honestly.

GPU limit: a VM installation/functional pass does not establish GPU inference performance. A VM with the model served by the host tests connectivity, not an entirely self-contained first install. Prefer installing a small CPU-capable model inside the guest for the self-contained functional test, then test the advertised GPU/model configuration separately on physical hardware. Report those results separately.

Sources: [VirtualBox downloads](https://www.virtualbox.org/wiki/Downloads), [Windows 11 Enterprise evaluation](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise), [Windows Sandbox requirements](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/). Windows Sandbox does not support this host's Home edition. The Enterprise image is a time-limited 90-day evaluation, not a permanent free Windows license.

## 6. Update README and release materials

Agent can do:

- Rewrite the README around what a new user can do now: experimental-alpha status, working download, tested Windows/model/hardware configuration, setup steps, expected downloads/disk use, capabilities/defaults, data handling, limitations, and troubleshooting.
- Replace unsupported performance/safety comparisons with demonstrated facts. Explain external services and opt-in integrations accurately.
- Reconcile source setup, installer setup, Python version, config defaults, and release docs. Confirm every linked asset is available to an unauthenticated visitor after publication.
- Prepare a short demo script and capture/sanitize screenshots or a recording if tooling permits; let Matthew choose whether to narrate it.
- Draft release notes, checksums, known issues, and a short announcement. State personal-project support expectations without promising continuous development.
- Choose a new version after reviewing changes; reconcile the older draft release without deleting it automatically.

Done when: a newcomer has one accurate route from README to the tested result. A polished video, paid certificate, and external benchmark are optional for this alpha.

## 7. Promotion and publication

Agent can prepare:

- Integration PR(s) into dev as needed, followed by the tested dev-to-master promotion PR. Attach created PRs to this chat.
- A final evidence note: exact SHA, artifact hash, scanner scope/findings, passing checks, VM outcomes, supported capabilities, remaining limitations, and proposed issue changes.
- Publication settings/checklist: visibility, branch protections, enabled checks, private vulnerability reporting, release asset, correct README link, and repository description/topics.

Explicit Matthew checkpoints:

1. Review candidate PR #722 into dev and promotion PR #723 from dev into master. Obtain explicit approval before either merge. Master is the in-app update channel, so merging there can affect existing installs.
2. Approve public visibility and release publication after privacy remediation and candidate validation. If a tag automatically publishes, pushing that tag belongs to this approval.

After approval: merge the approved candidate; publish the exact verified artifact; enable/check protection and CI; verify public clone/download/docs; apply approved issue edits; share the announcement only if instructed.

## Stopping rule

Release when the public surface is clean, the promised path works on clean Windows, the artifact matches its tested source, and Matthew has approved promotion/publication. New ideas go to the backlog. Fix only release-blocking regressions discovered by these checks; do not restart broad improvement work.

## Publication recommendation after the privacy audit — October 22

Prefer a new public repository containing a sanitized release snapshot, while
keeping this original repository and its complete history private. Current
source/discussion cleanup cannot remove older personal contact examples from
historical commits and retained PR references. This preserves recoverability
without promising that an ordinary force-push erases GitHub caches.

This is a recommendation pending the maintainer's choice, not authorization to
create a public repository. Prepare the tested source, installer, concise README,
checksums and known issues locally. After the repository name and publication
are approved, update its links/update remote, create the release, and verify
unauthenticated clone/download. Keep historical private integration PRs separate
from that publication action; either merge still requires explicit approval.

The maintainer approved removal of both obsolete releases/downloads and 37 exact
privacy-only discussion edits; all are complete and verified. The maintainer
also approved a one-time alpha code-organization baseline exception, recorded in
the module guide; limits and future-growth enforcement remain unchanged.
