# Qualification queue

Repository Actions has been enabled by the owner. After 104 focused native
cases passed for source `62546557807ee5d8ff468ea3d7d46ce1a090c8cf`, its canonical
branch push automatically started
[full run 42031063759](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759).
This verifies the automatic push trigger; no full workflow was manually started.
The device job succeeded. Simulator compilation and 270 TextFormat cases passed,
but startup smoke failed before an observed app launch. Full native integration
finished with 1,134 passing cases across seven suites; the Projection suite
executed zero cases because its XCTest host did not start. This is not a
qualified release.

The automatic build entry point is `ci/verify-latest`. Development branches and
PR updates do not automatically start hosted macOS work. Advance the verification
branch to the exact reviewed source batch once; do not create a throwaway commit
or retry a healthy run merely to get a different runner.

`archive-integration.yml` uses the global `ayugram-qualification` concurrency group
with `cancel-in-progress: false`. Its integration and unsigned device jobs may run
alongside the simulator startup build on that one revision. The simulator job
runs independently of unrelated suite failures; release qualification still
requires all integration suites to pass. A newer pending batch supersedes an older pending
batch while the active run is preserved. Standalone storage, compatibility and
legacy baseline workflows remain available through explicit manual dispatch.
These are diagnostic alternatives, not automatic duplicate runs.

## Observed queue failure on 2026-11-04

Job 126686204417 in run 41862050201 was canceled with no runner assigned and no
steps executed. Its public check annotation says:

> The job was not acquired by Runner of type hosted even after multiple attempts

The compatibility check 126685062470 in run 41861738456 also recorded:

> Due to capacity constraints, jobs targeting macOS arm64 runners may experience longer queue times.

These are hosted runner acquisition failures, not compiler errors, test passes,
missing signing profiles, or evidence of exhausted billing. The GitHub connector
can read runs/jobs and retry jobs but does not expose cancellation or check
annotations. These annotations were read from the public GitHub check endpoints;
no token or private credentials were exported. Active work is preserved.

Previously submitted runs retain their own workflow and concurrency settings;
changing YAML cannot move, cancel or reprioritize those existing jobs. Do not
claim the backlog was canceled or that GitHub guarantees FIFO priority. The new
policy prevents additional feature-push fan-out and limits subsequent submissions
to one active qualification plus its latest pending successor.

The manual `startup` scope replays an existing compiled simulator artifact.
It verifies the source run, successful compile step, outer and inner hashes,
platform and bundle identity, then rejects product-source differences in the
replay checkout. Tooling, this workflow and documentation may differ. The original
full simulator bundle repeatedly timed out during installation. A main-app-only
control, preserving the executable and resources, reached Telegram's actual
Welcome/Start Messaging screen. Embedded simulator entitlements still enabled a
valid App Group; absence from the outer signature alone did not establish private
storage. These diagnostic runs did not compile Telegram again.

Run 42073351166's private-only control timed out during installation before
seeding preferences or launching. Its image checker compiled and passed all
three controls. No device variant was published. Separately, a portable
subprocess reproducer proved a supervision defect: waiting for pipe EOF could
misreport a successfully exited command as timed out if a descendant inherited
its output descriptors. The corrected wrapper honors the direct child's exit
status, drains for a bounded 0.2 seconds and preserves output limits and actual
timeouts. This defect's role in that particular simulator timeout is unproven.

Run 42075783146 uses the corrected helper with the same compiled app, one fresh
simulator, the same 120-second install limit and unchanged two-launch private
storage gates. It passed both actual private authorization-screen launches and
published the separately named unsigned main-app-only device artifact
13438212892. All original device members outside PlugIns were preserved.
Explicitly seeding a private
selection in an empty disposable test container does not prove automatic
fallback or physical SideStore behavior. Replay cannot qualify the failed full
native suite. Its separate source-scoped queue preserves native qualification.

Run 42073979469 stopped at Bazel analysis because the diagnostic runner lacked
`testonly = True` while depending on its test-only capture hook. The runner
attribute was corrected without changing application code. Run 42075582797
validates that revision in `projection-only` scope; it is active at this
checkpoint. No full application rebuild was submitted for either tool repair.

The `focused` scope selects only PasscodeCore, archive and encrypted playback.
The `projection` scope retains those three suites plus Projection;
`projection-only` selects the single Projection suite for diagnosing its host.
All three scopes skip simulator/device application compilation. No throwaway
canonical-branch push is needed to diagnose a test harness.

An unsigned device artifact is build output only. Its source commit, device
Mach-O platform and SHA-256 must match its manifest; same-commit integration,
independent signing and physical iPhone validation remain separate gates.

## Historical repository-level permission blocker (2026-11-05, resolved)

The owner subsequently resolved the disabled-Actions setting. The failures
below are historical evidence, not a current request for manual dispatch.

The environment's authenticated `gh` client identifies as the authorized
`Livara-Dev` user. The canonical qualification workflow is active and its
`push.branches` includes `ci/verify-latest`. However, dispatching the prepared
startup revision returned HTTP 422: **Actions has been disabled for this
repository.** This explains the missing run; no runner was queued.

The explicit authenticated attempt to enable repository Actions with
`PUT /repos/Livara-Dev/Telegram-iOS/actions/permissions` and `{ "enabled": true }`
returned HTTP 403: **Resource not accessible by integration.** Reading the same
settings is also denied. The user has repository admin membership, but the
integration credential does not inherit every administration permission.
Enabling the individual workflow succeeds and does not enable repository Actions.

The supported CLI/API route is therefore available, but enabling repository
Actions requires a credential with the repository **Administration: write**
permission. Workflow dispatch requires **Actions: write**. No credentials were
printed, copied into the repository, or requested in chat. A YAML trigger change,
another throwaway push, or rerunning an old job cannot remove this setting.
The prepared startup fix remains on the verification branch.

The qualification dispatch helper uses normal `gh` authentication, verifies the
exact expected canonical commit, preserves existing runs, and verifies an actual
run appears after dispatch. It never reports an accepted dispatch as a started
build. Automatic future builds remain configured on canonical branch pushes,
subject to repository Actions being enabled and GitHub's normal event rules.

```sh
python3 tools/fork_baseline/dispatch_qualification.py --expected-sha <canonical-commit-sha>
```

The explicit `--enable-actions` option attempts repository enablement only after
GitHub reports Actions disabled. It preserves the existing allowed-actions
policy, then verifies enablement before one retry. A denied settings request
returns `repository_administration_permission_required`; it does not prompt for
or expose credentials. Normal `gh` authentication must be provisioned securely
by the environment, with no token values committed or passed on the command line.
