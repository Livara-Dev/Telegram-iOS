# Qualification queue

## Retained c55 qualification without rebuilding Telegram

Preserve the successful `c55e19c4` device build82 and simulator build1. The
`retained-modern` manual scope runs only `native-api-probe` and
`custom-options-artifact-replay`; every application, device, startup and Bazel
rebuild job is excluded. It shares the fixed c55 Projection queue with
`retained-clean`, and never cancels an active run. For a further native-only
diagnostic repair, `projection-plan-only` preserves the settings result and
executes only the modern native job. `custom-options-xctest` selects only the
retained renderer's Apple UI test job and shares the existing UI queue. Do not advance
`ci/verify-latest` for diagnostic tools or documentation alone.

The modern native job verifies the fixed captured products and their source/hashes,
then builds a tiny account-free Apple UIKit host and Swift XCTest control. This compiles
diagnostic code only. Both modern artifact jobs use freshly created devices
without a forced service restart. The separate `retained-clean` intervention
can refresh only the registered current-user service on an exclusive hosted
runner; it is not the ordinary replay setup. Xcode owns installation and launch
for the tiny control's one hosted identity test. A separate manual-launch
experiment is not a prerequisite for running real tests. After the control
XCTest attempt and verified control-device cleanup, and while source, plan,
signature and ownership checks remain valid, all 41 retained Projection cases
are attempted on a separate fresh device using that Xcode-generated host and
Swift-aware plan. The host is built with the asserted Projection identity from
the start. A private clone replaces only its diagnostic test with the complete
unchanged captured test bundle and copies the original presentation data and all
19 localizations. Resealing follows the actual validated Xcode signing inputs;
normalized host code, test bytes, resources and original products are checked.
This is explicitly a different harness: even a passing result would not qualify
the original Bazel host. No Telegram application or captured test is rebuilt. A failed
diagnostic control XCTest outcome is retained while the independent Projection
attempt can still run. Overall success requires both complete suites and cleanup; unknown
plan formats or unverified cleanup cannot become skipped passes. The control
is separate from AyuGram feature coverage. Both live observations use owned
device metadata and executable identity without relying on additional live
simctl requests; full bundle hashes are checked outside the observation budget.
Diagnostic markers and samples
cannot manufacture XCTest outcomes.

The modern settings job verifies the retained account-free host against five
unchanged source files and installs it on its own disposable iPhone 15 Pro /
iOS 27.0 simulator. It compiles only a standalone Apple UI test driver, with no
application target or dependencies, and preserves Xcode's generated test runner.
One new XCTest activates the unchanged host and requires the visible exact
“Custome options” title, hittable settings table and archive control. An actual
XCTest screenshot, recording, unchanged product hashes and verified simulator
deletion are required for success. The earlier CLI launch diagnostic remains
available under `custom-options-replay`; it is not a passing UI test. Neither
the new synthetic root test nor the historical eight controller and one UI test
proves authenticated full-app Settings navigation.

Only bounded, explicitly selected diagnostics and visuals are uploaded. Modern
native JSON is limited to 1 MiB per file, logs to 2 MiB and the combined selection
to 8 MiB. Settings evidence has separate 1 MiB JSON, 2 MiB log, 16 MiB image,
32 MiB recording and 64 MiB total limits. Symbolic links and nonregular files
are rejected before copying. Uploads require
a successful staging step even when the preceding native check failed. Raw test
plans, project products, simulator IPAs, credentials and account data stay private.
The diagnostic download token is absent from native child processes. A failed
qualification does not delete the separately successful device package.

## Historical qualification and general queue policy

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
with `cancel-in-progress: false`. For full/push qualification, the existing
`application-build` job runs all eight native integration targets before compiling
the simulator app in the same private Bazel session. A native failure stops the
simulator app build. The independent unsigned device job can run alongside it;
its output alone is not a qualified release. The standalone `integration-tests`
job is reserved for targeted/manual scopes, so full qualification does not
compile the shared simulator modules on two separate runners. No private
compiled cache leaves the ephemeral session, and no build-time claim of less
than ten minutes is made without measurement. A newer pending batch supersedes
an older pending batch while the active run is preserved. Standalone storage, compatibility and
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
completed in `projection-only` scope with thirteen passing cases after two
initial launch stalls/reboots. No full application rebuild was submitted for
either tool repair.

The `focused` scope selects only PasscodeCore, archive and encrypted playback.
The `projection` scope retains those three suites plus Projection;
`projection-only` selects the single Projection suite for diagnosing its host.
`native-only` runs all eight native suites without building the complete app;
`custom-options-ui` builds only the production-renderer fixture and its tests.
All targeted scopes skip simulator/device application compilation. No throwaway
canonical-branch push is needed to diagnose a test harness.

The combined full-mode native summary is a pre-build checkpoint: it records
`full_application_build_requested: true` and `full_application_built: false`.
The separate IPA qualification manifest records completed compilation. Native
logs and this checkpoint are retained even when the later app build fails;
neither a checkpoint nor successful compilation establishes physical installation.

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
