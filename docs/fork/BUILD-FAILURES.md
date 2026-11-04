# Hosted build failures and verification status

Updated 2026-11-04. These are observed failures, not proof of their underlying
cause or of successful fixes. Runtime, compilation, packaging and installation
remain separate acceptance gates.

| Revision / run | Observed result | Evidence and response |
| --- | --- | --- |
| Filter `6eba37ff`, run 41817303240 | First four suites passed 1,011 XCTest cases; projection host failed packaging because CFBundleVersion was absent. | Both bundle-version fields were added in `f295948a`. |
| Filter `f295948a`, run 41819900429 | Corrected host, projection module and test bundle compiled/packaged. Simulator/Xcode startup stalled; Bazel's 300-second allowance expired after 312.5 seconds, before any projection case executed. Remaining suites were not attempted by the former wrapper. | [Verified artifact metadata](evidence/filter-integration-f295948a.json). The target now has a bounded 900-second allowance for the pinned runner's three startup attempts. Tests-only collection attempts every fixed suite and still exits with failure if any suite fails. Core/capture suites run before the hosted projection suite. Native retry required. |
| Ghost activity `cd1effef`, run 41816366729, app job 126536387023 | Source compiled and linked, including SettingsUI, TelegramUI and application/extensions. Final app packaging failed re-signing embedded TelegramUIFramework with ad-hoc codesign: `internal error in Code Signing subsystem`. | [Verified artifact metadata](evidence/application-build-cd1effef.json). No compiler error, explicit disk-full error or missing signing identity was established. Initial free space was 36,462,518,272 bytes; final space was not retained. Resource changes described below require a new run. |
| Story `cc9f99df`, run 41821121479 | Swift compilation failed: the dispatcher changed its signal element from Void to Never before chaining a Void completion. Zero actual test cases ran. | [Compiler evidence](evidence/story-integration-cc9f99df.json). The chain now preserves Void while dropping intermediate values; a regression verifies completion-only retirement. Native retry required. |
| Earlier archive `1b7dfc3b`, run 41811248732, app job 126517384143 | Job ended with failure at 11:45:35 UTC while its simulator step remained marked in progress; device packaging and artifact upload never started. | Completed log retrieval returned BlobNotFound on repeated attempts. Only the earlier passing integration artifact exists. Cause remains unknown; do not invent a compiler error. |

The timeout XML contains one synthetic harness error, not one executed XCTest
case. No projection-test pass is claimed. The archive library separately passed
54 macOS tests and iOS module compilation at `f295948a`; those results do not
validate filter UI behavior.

## Resource containment and diagnostics

Each job uses a private Bazel output root, which already preserves incremental
build state across suite invocations. Keeping a second disk cache in the same
disposable directory can duplicate large artifacts without helping the next
job. The updated wrapper disables that redundant disk cache and records numeric
free/used/total disk space before and after execution, including failure and
before temporary-state cleanup. Remote cache/execution remain disabled.

This reduces avoidable disk use and improves failure evidence. It is not proof
that storage pressure caused the signing failure, nor proof that signing now
works. Normal simulator signing remains enabled. The pinned rules' optional
signing bypass is not used: its documentation warns that entitlement-dependent
features such as App Groups can behave differently.

## Remaining verification

Rerun all native suites on each changed PR head, then the full simulator and
independent unsigned-device jobs. Keep logs, checksums and manifests for the
actual completed revision. A successful source compilation does not establish
an IPA, a successful unsigned device package does not establish signing, and a
signature does not establish installation or runtime behavior on an iPhone.

## Runtime alignment for archive validation

The `e5d49047` job failed every suite: the first exited before producing test
outputs, subsequent runner invocations reported iOS 26.5 unsupported and listed
only 27.0. The extended projection attempt also failed before XCTest began.
The archive branch now explicitly pins all five test runners and preflight to
the installed iOS 27.0 runtime, without downloading 26.5. Xcode 27.1, source and
dependency pins remain unchanged. Runtime availability is checked before tests;
no fallback or successful native result is assumed.

## Original baseline completion and bundle diagnostics

Original run `41792955476`, job `126450334711`, is now reported by GitHub as
`cancelled` (updated 2026-11-04 13:37:22 UTC). This agent did not cancel it. No
artifact was retained and the completed-log endpoint returned `BlobNotFound`;
no specific compiler, timeout or cancellation cause is established. New archive
integration jobs remain independent of this original run.

A failed full-app build now attempts bounded, read-only diagnostics for the
application and embedded TelegramUIFramework before the private build directory
is removed. `bundle-diagnostics.json` includes executable size/mode, codesign
display/verification output and extended-attribute names only. It excludes
configuration files, credential values, signing keys, and bundle contents; paths
and API secrets are redacted. Unsafe paths and diagnostic failures are ignored
without changing the original build result. This is evidence collection, not a
signing workaround or an asserted fix for Apple's code-signing error.

## Media integration compiler failure

Run `41838526609` at `aba9fae9` passed 201 Postbox and 59 archive XCTest cases on
iOS 27.0. TelegramCore then failed Swift compilation in `mediaData` because the
outer signal implicitly captured `self` strongly and its nested closure captured
it weakly. TextFormat and the projection target also failed to build on this
shared dependency; no cases in those three targets executed. The explicit weak
outer capture now follows the existing page-reader pattern and returns a nil
result with completion when the coordinator is released. A lifetime regression
is included. [Verified failure evidence](evidence/archive-media-integration-aba9fae9.json) records the exact revision and checksum; the fix still requires native validation.
