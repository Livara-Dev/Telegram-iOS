# Qualification queue

## Retained c55 qualification without rebuilding Telegram

Preserve the successful `c55e19c4` device build82 and simulator build1. The
`retained-modern` manual scope runs only `native-api-probe` and
`custom-options-artifact-replay`; every application, device, startup and Bazel
rebuild job is excluded. It shares the fixed c55 Projection queue with
`retained-clean`, and never cancels an active run. For a further native-only
diagnostic repair, `projection-plan-only` preserves the settings result and
executes only the modern native job. `custom-options-xctest` selects only the
retained renderer's root UI test. `custom-options-interaction` selects only the
full twelve-stage settings interaction. `retained-interaction` combines that
full interaction with the modern native job and excludes the already verified
root case. It shares the c55 Projection queue; the UI-only scopes share the
existing UI queue. Do not advance
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

Owned app and data-container observations are independent and precede the
process-query check. An unavailable process query cannot suppress filesystem
evidence or authorize sampling. Closed lifecycle/reason fields and command
elapsed milliseconds distinguish an untriggered observer from a failed probe;
raw process data, paths and samples stay private.
The fixed app/data inventory cap is 512, with deadline-aware live scans. A real
iOS 27 data inventory exceeded the earlier 128-entry cap in `42239698766`;
unknown, truncated or late inventories cannot assert absence or uniqueness.

The modern settings job verifies the retained account-free host against five
unchanged source files and gives Xcode its fixed `UITargetAppPath` and dependent
product for installation on an owned disposable iPhone 15 Pro / iOS 27.0 simulator.
There is no manual `simctl install` prerequisite or pre-execution installed-byte
claim. Full installed bytes, Info and strict signature must be attested after
XCTest and before qualification. It compiles only a standalone Apple UI test driver, with no
application target or dependencies, and preserves Xcode's generated test runner.
One new XCTest activates the unchanged host and requires the visible exact
“Custome options” title, hittable settings table and archive control. An actual
XCTest screenshot, recording, unchanged product hashes and verified simulator
deletion are required for success. The earlier CLI launch diagnostic remains
available under `custom-options-replay`; it is not a passing UI test. Neither
the new synthetic root test nor the historical eight controller and one UI test
proves authenticated full-app Settings navigation.

The full interaction profile compiles the original SHA-pinned interaction source
into the same private driver. The application bundle initializer and launch
method change. A third, exact reversible substitution repairs only the empty-search
keyboard visibility contract: the same drag and five-second wait require absence
or valid non-hittable, offscreen keyboard geometry, with initially visible geometry
required before the drag. Invalid geometry fails closed. All other assertions and
the original source file stay intact. This harness repair still needs native proof. One fresh
case must supply twelve complete named screenshots, twelve frame records and
eleven observed UIKit appearance records, in addition to all source, installed
bundle, recording and cleanup proofs. Its case does not explicitly assert the
root navbar title; the separate root-test origin is retained. This still uses
synthetic account callbacks and never qualifies authentication or account bindings.

The single UI-test command has a bounded 600-second installation/startup/test
allowance. Run `42236670490` showed visible Apple runner installation progress
late in its original variable-rate recording before the previous 300-second
attempt ended without a case start. Video timestamps are not aligned Xcode wall
times; this motivates one longer controlled observation, not a confirmed cause
or guaranteed fix. The root UI case later passed in `42239698766` with a measured
167.376-second whole command and a 23.351-second case, so the longer allowance is
not established as the cause of that success. Projection's app was absent at
the early observation but unique after its 300-second timeout; its next controlled
command has a 600-second bound too. Run `42243934171` then stalled at the
separate 240-second boot-status prerequisite before any XCTest command. The next
profile retains a required explicit boot acknowledgement and identity checks,
but delegates readiness, installation, launch and testing to each single
600-second Xcode command. Its destination-discovery allowance is bounded by that
same command lifetime; an acknowledged boot is never reported as ready. The native
job has a 35-minute ceiling for the two fixed commands, driver compilation and
mandatory cleanup. There are no assertion retries or automatic timeout increases.
Both profiles retain strict pass, immutable-product and owned-cleanup requirements.

Run `42243934171` executed the full settings case and failed its empty-search
keyboard-existence assertion after three light-stage observations. Installed
bytes/signature and owned-device cleanup passed. All 743 original video frames
were reviewed: the interactive drag visibly dismissed the key grid, but XCTest
continued reporting the keyboard element present. This supports the explicit
visibility-contract repair above; it does not establish a UIKit or OS root cause.
Dark stages and reset were not reached. The successful root test and device
package remain separate, preserved results.

Run `42247024748` then passed the actual hosted control and cleaned up both
native simulators after removing that prerequisite. Projection did run one
600-second command, but still started zero of 41 cases. At 90 seconds its app
and data containers were absent; both were unique after timeout, with no launch
marker. This does not establish the startup cause. The separate UI case reached
only root-light and failed when its archive switch stayed off after a tap; the
keyboard repair was not reached. All 229 video frames and ten flagged changes
were reviewed. The fixture saves synchronously, so no save-delay cause is claimed.

The next UI-only profile has a fourth exact private-source substitution: it
requires initial off state, a hittable enabled switch wholly inside the window,
records finite geometry, sends one explicit center-coordinate tap and retains
the exact on-state assertion plus a synthetic callback confirmation. A fixed
five-second state wait stays within the existing 600-second command. There are
no toggle retries. This is an unverified touch-delivery experiment, not a
confirmed application repair. If full attachment collection is incomplete, one
strictly validated original root attachment may be retained as diagnostic-only
`root-light.png`; it cannot satisfy any twelve-stage qualification gate.

The next native-only diagnostic moves just modern Projection’s one live probe
from 90 to 480 seconds; the earlier probe ran before app installation. The control
and historical profiles keep 90 seconds. The command remains bounded to 600
seconds and the observer to 18 seconds, with no second callback or test retry.
Runner-owned planned/actual trigger timing is published as closed metadata;
case starts, exit and the deadline suppress inappropriate observations. This
change is prepared, not a successful native outcome or a diagnosed cause.

UI-only `42250781611` at driver `69eba4c6` subsequently passed the actual full
case and twelve PNG, twelve frame and eleven appearance proofs, unchanged installed/source
bytes/signatures, recording completion and owned cleanup. Both manual reviewers
covered all 2,599 original frames/226 flags and twelve native PNGs. The bounded
public log truncates; complete-input parsing remains the outcome authority.
The driver and inputs are unchanged at the later native-only diagnostic head.
Do not repeat the passing UI job for a native-only tooling or documentation
change; its account-free and visual-quality limits remain in the feature matrix.

Native-only `42251953229` exercised the later probe at 480.068 seconds. Its
control and both cleanup gates passed; Projection still started zero of 41 cases
before 600.239 seconds. The live host’s owned path/UID/executable were verified,
its app/data containers were unique and its marker absent. Sampling timed out;
no stack or cause was established, and full installed-tree attestation was not
completed. Do not label that missing attestation as tampering.

The separately selected `projection-deferred-load-only` scope runs only the
native diagnostic job and shares the fixed c55 queue without cancellation. Its
prepare step alone receives the artifact token; execution receives none. It
builds diagnostic control/wrapper code and preserves the compiled feature bundle.
The wrapper loads it through public APIs after active UIKit and must provide
41 real leaf outcomes, its own separate outcome, matching public run counters
and Xcode result data. This is an experimental different harness, with no
production or original-host qualification implied. The ordinary 41-case parser,
settings success and successful device build remain preserved. No deferred
native execution is claimed until its reviewed profile actually runs.

The first deferred run, `42260617887` at `ae19a4dc`, failed during preparation
after 193 portable preflight cases passed. No compiler or native suite ran.
Local reproduction identified `application_source_changed`: preserving the
concurrent exact-source snapshot workflow required edits to two audit-tool files,
and those exact tooling paths were missing from the retained-source allowlist.
The fix names only `tools/fork_audit/check.py` and its exact test file; neighboring
paths remain rejected. The actual checkout guard now runs before the modern/deferred
portable preflight and artifact preparation. Full preparation passed locally using real GitHub provenance
metadata and the cached, hash-verified captured artifact, with zero application
builds or native executions. Closed preparation failures now retain bounded
reasons without exporting arbitrary exception text or replacing prior evidence.

Run `42262815237` at `b3fe7c2f` passed preparation and all 198 portable preflight
cases. The baseline control compiled and its signing checks passed. Xcode then
rejected the wrapper scheme's external test host during `build-for-testing`,
before Swift compilation or any simulator creation. No native case executed.
The build-action repair compiles only the wrapper target with ordinary `build`,
fixed private product/intermediates paths and the same validated external
`TEST_HOST` and `BUNDLE_LOADER`. It does not build another host or change the
app-hosted execution plan, captured payload or Swift source. Actual macOS
compilation and the 41 feature outcomes remain required.

Run `42263827970` at `a9bede96` also passed preparation and all 198 preflight
cases. Its ordinary wrapper build reached a more specific Xcode constraint:
the host's PlugIns directory was outside the wrapper's target build directory.
The wrapper still did not compile, and no simulator or native case started.
The layout repair uses a private copy of the validated baseline host in the
wrapper product root and Apple's bounded private build-settings output to
identify the actual test product. Original baseline and captured products remain
immutable; final execution still stages from the untouched baseline host.
This repair requires actual macOS validation before claiming the 41 outcomes.

Run `42266084971` at `d7cb7ac4` validated the repaired hosted PlugIns layout:
the wrapper compiled, signing passed and the separate control passed one case.
The unchanged retained payload then executed all 41 cases successfully, with a
passing wrapper and matching observer/public run counters. Installed bytes,
original products and both cleanup gates passed. This is experimental assertion
evidence, not a successful qualification: Xcode exited 65, and strict native
42-case result accounting rejected the case document. The public log clipped
the final error, so nested-suite warnings alone cannot establish its cause.
The next diagnostic retains bounded redacted head/tail logs and closed JSON
structure/rejection counts. Exit status, accepted schemas, all case counters,
source/signature and cleanup gates remain unchanged.

The instrumented repeat `42269497024` at `cb5acc9b` retained the missing tail.
All 41 payload methods and the wrapper report success, followed by XCTest IDE
session completion/failure-recording assertions and termination. Its automatic
restart ran zero tests. Native metadata has 42 known identities, 41 successes and
one failure; strict parsing independently rejects an array shape. Xcode exit 65
remains fatal. Do not repeat the unchanged nested-execution profile or equate its
wrapper stdout pass with an Apple result pass. Standard discovery is the next
separate experimental profile; every native result and integrity gate remains
required.

The explicit `projection-discovery-only` scope selects
`--execution-profile discovery` for both preparation and execution. It compiles
only a new diagnostic wrapper, preserving the original f144 wrapper and all
captured feature bytes. Public `defaultTestSuite` returns the validated payload
hierarchy followed by its verification case; XCTest performs all execution.
Process/platform identity and the owned host's `sceneVisible` marker precede
UIKit or payload access. Background synchronization is bounded; main-thread
discovery without the marker fails closed. Native exit zero, all 42 sequential
case outcomes, observer/run counters, exact native result records, signatures,
source immutability and cleanup remain mandatory. Portable fixtures do not prove
that the pinned SDK compiles or runs this discovery lifecycle. This profile
does not build Telegram or an IPA and retains the same no-cancellation queue.

The retained c55 diagnostic continues on `diagnostics/c55-projection-deferred`.
Concurrent source-publication/build workflow additions on `integration/ayu-current`
are preserved on that branch. The isolated diagnostic avoids changing their
source or admitting unrelated new workflows into the fixed c55 replay scope.
It still shares the same no-cancellation Projection queue and runs no full app
or device build. Its results qualify only the explicitly retained c55 payload.

Only bounded, explicitly selected diagnostics and visuals are uploaded. Modern
native JSON is limited to 1 MiB per file, logs to 2 MiB and the combined selection
to 8 MiB. Settings evidence has separate 1 MiB JSON, 2 MiB log, 16 MiB image,
32 MiB recording and 64 MiB total limits for the root profile. The explicit full
interaction profile permits a 128 MiB recording and 256 MiB combined selection,
including only the twelve fixed screenshot names and bounded observation files.
The historical full recording was about 76.5 MiB. Symbolic links and nonregular files
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
