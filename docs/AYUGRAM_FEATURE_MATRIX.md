# AyuGram native iOS feature matrix

Audit date: **2026-11-06**. The current compiled candidate uses application source
`c55e19c4f5cbe0d730499035d0a0ef712f6903a9`. Historical baseline entries refer to
`62546557`, `73b52244` and other explicitly named revisions; they do not verify
later feature changes. The checkpoints below distinguish implementation, native
tests, full application compilation and actual startup. No feature is marked
fully working without relevant real-application evidence. Simulator behavior does
not establish physical-iPhone installation, device performance or feature parity.

The [dated reference audit](fork/REFERENCE-AUDIT-2026-11-05.md) verifies the requested
**November 4, 2026 Android build 20261104**, based on exteraGram 13.1.1. Its changelog
references the exteraGram November 2 announcement. Public AyuGram4A source is
still July 2023 (`70131456`), Desktop is October 20, 2026 (`0f2a31bb`), and the docs
source is January 2025 (`42f5f32c`). This is an inventory of discoverable public
features, not a claim of complete corresponding source for the current Android
binary. No proprietary exteraGram components are copied.

## Current verification evidence

- Historical application source `62546557` produced the full unsigned ARM64 device build in
  [42031063759](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759).
  Seven native suites executed **1,134 passing cases**; its original Projection
  host ran zero cases. That full run failed, so it is not an all-green release.
- The same Keychain/archive/playback modules passed **104 native cases** in
  [42027769348](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42027769348)
  and again at c484 in
  [42058341829](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42058341829).
  Counts: 16 PasscodeCore, 77 LocalMessageArchive, 11 encrypted playback. The deprecated
  API and video failures are repaired; neither remains a current open failure.
- [42075582797](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075582797)
  executed **13 native Projection cases, all passing**, at f48c9b04. Two startup
  stalls/reboots preceded success; stable first-attempt launch and real failed
  test-product capture were not established. This pass must not be described as
  a new full application qualification.
- [42075783146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146)
  passed two actual authorization-screen launches in one fresh simulator, with
  a private selection explicitly seeded for the test. The app created private
  account metadata and retained the same directory across restart. This is not
  automatic-fallback, Telegram-login, message-preservation or physical-device proof.
  That historical [main-app-only unsigned device artifact 13438212892](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146/artifacts/13438212892)
  omits six extensions and preserves all 880 retained members from the original
  device package. It requires independent signing and device testing.
- The user's later report says the current IPA launches but remains visually
  similar to stock Telegram. That is user-reported startup evidence, not a
  demonstration of the hidden/default-off feature paths. No new feature batch
  or customized UI is claimed verified by the older startup recording.
- [42097515377](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42097515377)
  compiled the exact production options renderer and passed eight controller
  tests plus one real XCUI scenario in an account-free host. Screenshot export
  then failed with exit 64; the overall workflow did not pass. Its variable-rate
  recording and 17 checksum references were retained and verified. Frame review
  found that the dark-labelled scenes were actually light: the empty-search
  keyboard covered the fixture's appearance control. Dark UI is not verified by
  that pass. The follow-up tests require a hittable control, keyboard dismissal,
  and observations of the actual window/table traits and resolved table color.
  Light layouts and the synthetic reset/cosmetic actions were visually observed;
  recorded navigation gaps up to 248 ms do not establish smooth device scrolling.
- [42098718967](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42098718967)
  completed at b24d8688 with **201 Postbox cases passing**. The other seven
  suites did not execute: dependent builds exposed one explicit/weak capture
  error in the archive coordinator, two completion-only signal type errors,
  and one missing SQLCipher progress-callback symbol affecting both archive
  test bundles. These are build failures, not encrypted-playback runtime
  failures. The follow-up preserves capture lifetimes, emits no fake signal
  values, and enables the existing callback by removing only its omit flag
  from Bazel/SwiftPM. All encryption flags and dependency versions remain
  unchanged. Native requalification is required; portable ABI contracts alone
  do not establish successful linking or cancellation behavior.
- [42106358713](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42106358713)
  compiled the repaired renderer harness and executed eight unit cases at
  b490ba13: seven passed. The positive actual light/dark table/window/color
  checks passed, but the appearance test's deliberate light-trait negative
  control sampled UIKit before its update cycle and failed. The follow-up
  waits for that actual transition with a bounded run-loop condition while
  retaining the original assertion. XCUI interaction, video and screenshot
  export were not attempted in this run; visual dark-mode proof is still open.
- [42114628197](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42114628197)
  at b492f097 passed **965 completed cases**, failed six, and crashed during one
  further Core case before it produced an outcome. Counts: Passcode 16,
  Postbox 201, archive 99, encrypted playback 18, TextFormat 270 and Core 361
  passing / 6 failing. Later Core classes never ran. The 18 playback cases
  actually decoded the generated H.264/AAC video, AAC and Ogg/Opus voice, MP4
  animation, PNG/JPEG, animated GIF and PDF fixtures. SettingsUI ran zero cases
  because its PDF controller's stored `next` button collided with UIKit's
  inherited property. Projection also ran zero cases: three XCTest bootstrap
  stalls ended with exit 12, although its host launched standalone. The Core
  failures concern deletion/unlock and five transfer fixtures; a later media
  filter case hit Postbox's unsupported single-value Codable container. These
  remain repair/qualification work, not an all-green application build. The
  artifact's outer SHA-256 and 94 inner checksum references were verified.
- [42116837030](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42116837030)
  at fd7be3a0 compiled and packaged the production renderer host, then timed out
  waiting for its new simulator to boot. It ran **zero unit/XCUI cases** and
  produced no recording or screenshots. Its outer checksum and seven inner
  references were verified. The trait negative control and screenshot exporter
  were not exercised. The follow-up keeps the existing boot timeout and permits
  one restart of that run's new empty simulator, retaining both attempts and
  the original failure; this is not a first-attempt reliability claim.
- The UI job in [42134690056](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42134690056)
  also compiled the host, then timed out during boot and during the owned
  simulator's shutdown retry. It ran zero tests and recorded no video. Both
  direct command processes remained alive at their deadlines; the old pipe-EOF
  defect is not the recorded cause. Its outer checksum and nine inner references
  passed verification. The native job in that run continues independently.
  A tooling-only follow-up moves boot before compilation and uses the same
  combined `bootstatus -b` preparation as native preflight. This removes known
  ordering differences; the underlying host cause and recovery remain unproven.
- [42138477547](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42138477547)
  at 5ba6d074 booted its owned simulator on the first attempt and passed all
  **eight controller tests**, including the repaired actual-appearance negative
  control. The XCUI scenario failed after four recorded stages: the Search
  action dismissed the keyboard between its existence check and frame query.
  This is a test race, not evidence of a production renderer failure. The
  root-dark observation sampled dark window/table traits and resolved color;
  the remaining eight stages and screenshot exporter were not reached.
  The original variable-rate video was retained. The outer artifact checksum
  and all nineteen inner references passed verification. The follow-up removes
  the disappearing-keyboard geometry query while retaining the strict hidden
  keyboard, hittable-control and actual-appearance assertions. A passing rerun
  is still required; this run does not qualify the complete application.
- [42142928776](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42142928776)
  at 173ca877 passed **eight controller cases and the complete XCUI case**,
  with no failures or skips. All twelve stage records and eleven actual
  appearance observations were present; recording exited successfully. The
  workflow subsequently failed when Apple's attachment exporter tried to move
  `database.sqlite3` into the read-only Bazel `test.xcresult`. This is an
  evidence-export permission failure, not a failed app interaction. The outer
  checksum and twenty-one inner references passed verification; the original
  variable-rate video is retained. The follow-up makes a bounded writable
  private copy for export without modifying Bazel's original results or
  publishing raw result bundles. Original-frame review observed all twelve
  states, including actual dark search/category, reset cancellation and
  confirmation, and cosmetic changes/reset. Coarse recorded navigation steps
  do not establish smooth device performance; the dark cosmetics accent text
  needs a dedicated contrast review. Actual Apple export must pass in a rerun.
- The native job in [42134690056](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42134690056)
  at 17033b09 finished with **862 completed passing cases, one failed case and
  one further case aborted before an outcome**. Passcode 16, Postbox 201,
  archive 114, encrypted playback 18 and TextFormat 270 all passed. Archive
  breakdown: 79 Store, 24 Transfer and 11 Keychain cases. Core passed 188 cases,
  including all 41 coordinator cases and the real Postbox edit/delete → saved
  rows/encrypted payload → cache cleanup → reopen fixture. Its next absent-store
  fixture overfulfilled one XCTest expectation when independent startup wakes
  each probed the missing archive. SettingsUI compiled and passed 55 cases;
  the one failure incorrectly expected a diacritic-folded `a` query to match
  nothing. Follow-up changes correct those test contracts without changing
  production code, preserve every read-only/key-creation assertion, and add two
  Unicode boundary cases. They still require native execution.
  Projection executed zero cases after bootstrap stalls; no actual launch cause
  was retained because the device inventory timed out before downstream probes.
  Sanitized compiled test products were retained for a bounded fresh-host replay
  without another compile. Only 26 of that revision's 124 new methods completed
  successfully; the other 98, including later Core classes, remain unqualified.
  The artifact's outer SHA-256 and all 96 inner references were verified.
- [42147021107](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42147021107)
  at c5eafa23 **passed the complete renderer workflow**: eight controller cases,
  one XCUI case, twelve stage records and eleven actual appearance observations,
  with no failures or skips. Apple's exporter exited zero with no stderr; all
  twelve real PNG attachments were exported and passed the nonblank check.
  The original variable-rate video is retained. The outer artifact SHA-256 and
  all 47 inner references were verified. This closes the writable-copy export
  defect without changing production rendering. It remains an account-free
  settings fixture, not complete-application startup or physical-device proof.
- The automatically triggered full run
  [42152205369](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42152205369)
  at dc68f12f passed **1,116 cases and failed four**, with no crashes or skips,
  before its application gate stopped. Passcode 16, Postbox 201 and archive 114
  passed; Core completed 785 passing and four failing cases. Both deleted-history
  integration cases now pass. The later playback/TextFormat/SettingsUI/Projection
  targets and full simulator app were not run. Of the 126 new methods, 100 passed
  and 26 were not reached. Three import-facade cases returned transient
  `unavailable` while startup storage work was still in flight. The fourth
  fixture attempted to construct a current typed model from a future schema
  before testing its opaque Postbox preservation. The next source queues one
  explicit transfer at a safe storage boundary, revalidates all authority, and
  adds seven regressions; the future-policy fixture now exercises the real
  unsupported-entry path and every mutator. These repairs await native execution
  in the new fixed `core-only` scope before another full qualification. The
  artifact's outer digest and all eighteen inner references were verified.
  The preserved ARM64 device job subsequently succeeded and published
  [build 79, artifact 13476251721](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42152205369/artifacts/13476251721).
  Its IPA SHA-256 is
  `7c44f61b5940ca9df4ea70dac5fb737b32a13621150cf4b2a719912b5df1543d`.
  All twelve binaries are unsigned ARM64 iPhoneOS; the five frameworks resolve
  across seven executable contexts. Active metadata includes the stable
  `AyuGramBaseBundleIdentifier`. All eleven outer-package checksum references,
  including the three exact-source/license materials, were verified. This is
  the failed run's build candidate, without a matching simulator application or
  startup result, and is not a qualified replacement for the installed app.
- [Core-only run 42157989839](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42157989839)
  tests source `72b7c6b4` before another full build. Subsequent tool-only source
  `644ccb94` adds a shared publication/replay gate for every embedded device
  binary, required executable-bundle metadata and actual loader/RPATH dependency
  closure. Nineteen malformed-package regressions and all **430 portable checks**
  pass; independent review is clean. The new gate also passes against build 79
  without modifying its bytes. Static package checks do not establish signing,
  system-library availability, dynamic loading or successful application startup.
- Full source `c55e19c4` in
  [42160468600](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42160468600)
  passed **1,473 actual native cases**: Passcode 16, Postbox 201, archive 114,
  Core 796, encrypted playback 18, TextFormat 270 and Settings 58. There were no
  assertion failures, skips or crashes. All twelve Core transfer cases and the
  four previous failing cases passed. Of 133 new methods, 114 passed and nineteen
  Projection methods were not reached. The Projection host exited 12 before any
  of its 41 cases started or a result bundle was created; a post-failure device
  inventory also timed out. This does not establish a signing root cause. The
  outer native artifact digest and all 96 inner references were verified.
  Full simulator compilation and launch smoke were blocked in that original
  full job. The later simulator-only build below does not change its failed result.
- The same source produced **unsigned device build 82**:
  [artifact 13479087810](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42160468600/artifacts/13479087810),
  IPA SHA-256
  `addaaf68000d010b84cea0d9798b9a234f43bfabc62845cdbf18fc5f14c4605e`.
  All twelve unsigned ARM64 iPhoneOS images, five frameworks, 113 dependency
  edges, eleven checksum references and three source/license materials were
  independently verified. Stable bundle metadata is present. This establishes
  device compilation, not installation or successful application startup.
- The simulator-only diagnostic,
  [42174474046](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42174474046),
  successfully compiled the full unchanged `c55e19c4` application and passed all
  **270 TextFormat cases**, with device compilation disabled. Its simulator-only
  [artifact 13490005243](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42174474046/artifacts/13490005243)
  is build **1**, with IPA SHA-256
  `df7a313e263440d92f2ab503e7ee1c1afb328774f6d631b3d052169f2f2e8e10`.
  This repeats a suite already counted in the 1,473 native passes; it does not
  execute Projection's 41 cases or supply full native qualification. Any later
  startup/device pairing must retain this simulator origin and the distinct
  device-build-82 origin at the same source. This build alone did not verify
  authorization startup; the later replay below supplies that bounded simulator
  evidence. Physical-device behavior remains unverified.
- The live synthetic-host diagnostic
  [42181777928](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42181777928)
  verified the owned Xcode PID during its bounded observation. The 3-second app
  container query and 10-second sample command both timed out with zero returned
  bytes. No container evidence or wait stack was obtained; other process roles
  were explicitly unmeasured. XCTest reached its unchanged 300-second limit with
  **zero of 41 cases started**, without assertion retries. No raw process sample
  or process-argument/environment dumps were published; private paths were redacted.
  These results do not establish host absence, a deadlock or a startup root cause,
  and do not turn the failed original qualification into a passing result.
- Current-source [startup replay 42191212317](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42191212317)
  passed preflight, exact-origin hashes, source signatures and main-app-only
  code/resource preservation, then `simctl install` exceeded its unchanged
  **120-second** bound. No application launched; no screenshot, recording or
  derived device variant was produced. All five diagnostic checksum references
  were verified. This is an installation blocker, not evidence of an application,
  authentication or Keychain failure. The historical diagnostic below does not
  replace the current startup gate.
- The fixed historical comparison
  [42194046857](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42194046857)
  could not reinstall the previously launching `62546557`/build54 package:
  installation timed out at 120 seconds; shutdown and deletion each timed out
  at 30 seconds. The safety gate correctly skipped the current arm. Both artifact
  digests and six historical plus two current checksum references were verified.
  No app launched, no recording exists and no new extension-free device variant
  was published. That comparison was blocked by hosted installation/cleanup;
  it did not prove an application cause, global deadlock, physical repair or
  successful current startup.
- Current-source [iPhone 15 Pro startup replay 42204637743](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743)
  passed both launches on the iOS 27.0 simulator without recompiling application
  code. The main-app-only control explicitly seeded fresh **private storage**;
  `authorizationVisible` was reached after **5.97 and 4.99 seconds**, with the
  same private account-store directory retained after restart. Both 1179×2556
  screenshots and all 390 original variable-timestamp video frames were reviewed:
  the AyuGram welcome screen, Settings → Custome options guidance and complete
  Start Messaging control are visible on both launches. White launch intervals,
  SpringBoard transitions and Dynamic Island changes are documented; no persistent
  black app screen or diagnostic overlay appears. Startup
  [artifact 13495439062](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743/artifacts/13495439062)
  has nine verified checksum references. This verifies the explicitly pinned
  private-storage simulator control, not automatic App Group fallback, Telegram
  login, SideStore signing, Apple HTTP 403 resolution or physical-iPhone behavior.
- The same replay published the current-source
  [main-app-only unsigned device artifact 13495848146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743/artifacts/13495848146),
  IPA SHA-256
  `2cb541e76eb632b5b66c24a5e1937b6042cc230551c418948d75291f69e45cc8`.
  It derives from device build 82 / run 42160468600, paired with simulator build
  1 / run 42174474046 at the identical `c55e19c4` application source. Six
  extensions were removed; all **880 retained entries match both bytes and ZIP
  metadata**. The six unsigned ARM64 iPhoneOS images comprise one executable and
  five frameworks; all nineteen bundled dependency edges resolve. Five checksum
  references and all three exact source/license materials were verified. This is
  a new unsigned package of existing application code, not a new device compile,
  signed installable IPA or physical-device startup proof. The full build-82
  package remains available separately.
- The paired [iPhone 15 Pro Projection replay 42204637743](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743)
  retained the exact c55 test products and strictly resealed a private host copy,
  but XCTest timed out at the unchanged **300-second** limit with **zero starts
  and zero outcomes among all 41 expected cases**. There were no assertion
  retries; installed-host attestation was unavailable, and cleanup completed.
  [Artifact 13496167308](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743/artifacts/13496167308)
  has four verified checksum references. This replay does not establish a fixed
  test host, passing presentation assertions or a signing/root-cause diagnosis.
  The [bounded evidence record](fork/evidence/qualification-c55e19c4.json) includes
  the completed full unsigned device candidate and all remaining qualification gates.

## Core features and native integration

### Current candidate overlay

The overlay below records the compiled `c55e19c4` candidate. Its original native
qualification executed **1,473 passing cases across seven suites**, while all
**41 Projection cases remained unexecuted**. The separate simulator build and
unsigned device build 82 establish compilation. The later two-launch iPhone 15
Pro simulator replay verifies the welcome screen using explicitly seeded private
storage; authenticated application behavior remains unverified. The
[feature evidence record](fork/evidence/feature-verification-c55e19c4.json)
maps features to exact passing test classes, counts, representative methods and
published-log hashes. Feature groups overlap; their counts must not be added to
the suite total. Historical checkpoints and the older 62546557 IPA do not verify
later features. New diagnostic results retain their own origins in the
checkpoints above.

Later artifact-only diagnostics retain separate results in that evidence record.
[42216084535](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42216084535)
verified the corrected XML signature readback, but its manually signed tiny
control timed out before its one XCTest began; Projection was not attempted.
[42217329564](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42217329564)
verified the owned settings simulator and complete installed bundle/signature,
but launch and cleanup commands timed out before any actual UI capture. These
results identify CI boundaries; they add no feature-test passes or physical
installation evidence. The original compiled applications and successful
welcome-screen startup evidence remain preserved.

[42233885595](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42233885595)
passed the independently built Swift hosted control's one actual XCTest and
verified its simulator cleanup. The same run preserved the complete captured
Projection test bundle and presentation resources in a separately identified
Xcode UIKit host, but **zero of 41 cases started** before its 300-second limit;
that device's cleanup passed too. The control is diagnostic evidence, not new
AyuGram feature coverage. The alternate harness did not execute or qualify the
original Bazel host, and no wait-stack or startup cause was established.

The settings driver compiled and passed signature checks in that run, but its
manual app-install command and cleanup timed out before XCTest. The subsequent
[Xcode-managed installation replay 42236670490](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42236670490)
reached `Testing started` yet started **zero of its one new UI case** before the
300-second bound. The target settings bundle was unavailable afterward; shutdown,
deletion and exact simulator absence were verified. Its actual screenshot and
all **88 original video frames** were reviewed: only SpringBoard and a
runner-labelled installation are visible, with no “Custome options” interface.
The 217.306667-second recording became ready after the launch request, so its
timestamps are not aligned Xcode wall times or proof of whole-launch coverage.
Visible installation progress motivates a separately bounded 600-second UI
experiment; it does not establish that the old limit caused failure. None of
these failed attempts adds feature-test passes or physical-device evidence.

[42239698766](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42239698766)
then passed **one new native root UI case** against the unchanged retained
renderer. The foreground state, exact “Custome options” navbar title, hittable
settings table and archive control passed actual XCTest assertions. The case
took 23.351 seconds; the whole native command took 167.376 seconds under its
600-second bound, so the old timeout is not established as the previous failure's
cause. Full installed bytes/Info/signature, unchanged source/runner, recording
completion, shutdown, deletion and exact simulator absence all passed. Two
reviewers inspected the actual PNG and all **214 original video frames**:
frames 188–191 (77.690–83.980s) show the expected initial root. A roughly seven
second white launch interval precedes it, and the fixture toolbar overlaps the
partly offscreen following category row. This is fresh initial-root evidence,
separate from the historical full interaction; it does not verify scrolling,
dark appearance, account callbacks, authentication or a physical iPhone.

That run's separate Swift hosted control passed too, but Projection still
started zero of 41 cases before 300 seconds. Its app inventory changed from
absent at the early live observation to one verified candidate after timeout;
the data inventory exceeded the diagnostic's 128-entry cap, so no launch marker
or wait stack was established. Both native simulators were cleaned up. The next
controlled Projection profile uses a 600-second bound and deadline-aware,
512-entry inventory; all assertions and pass/signature/cleanup gates remain
strict. No result of that next attempt is implied here.

Run [42243934171](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42243934171)
then stalled at the separate boot-status prerequisite before either native test
command. Its 41 Projection cases remained unattempted and native cleanup timed
out. The full settings interaction did execute one case but failed after three
light-stage records at keyboard-existence waiting; installed integrity and UI
cleanup passed. All 743 original video frames show the key grid visibly
disappearing after the drag. A narrowly derived harness now checks actual visible
keyboard geometry with the same gesture and five-second bound; this is not a
passing native result. The next native lifecycle keeps explicit boot request
acknowledgement while delegating readiness to Xcode within its fixed command
budget. No application or captured feature test is rebuilt.

| Candidate change | Current evidence | Remaining qualification or boundary |
| --- | --- | --- |
| Welcome-screen startup and current unsigned device package | [42204637743](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743) passed two actual full-app simulator launches with explicitly seeded private storage and stable account-store directory after restart. Both screenshots and all 390 video frames were reviewed. The main-app-only device artifact retains all 880 retained entries byte-for-byte with matching metadata | Simulator iPhone 15 Pro / iOS 27.0, not physical iOS 27.2. Automatic fallback, login, Apple HTTP 403/signing and installation remain unverified. The device IPA is unsigned; six extensions are absent. Device build 82 and simulator build 1 retain distinct origins; application code was not recompiled |
| Prominent **Custome options**, eleven searchable categories, quick toggles | [42147021107](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42147021107) passed 8 controller tests and 1 complete twelve-stage XCUI scenario. [42239698766](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42239698766) separately passed one fresh root UI case, installed-bundle/signature/source/cleanup proofs, and manual review of all 214 frames. Production renderer sources are byte-identical at `c55e19c4`; the fresh root case does not rerun the historical nine tests | Synthetic account-free host. The full app reaches the welcome screen and has a source-wired leading Settings entry, but authenticated entry/routing remain unverified. The fresh full interaction failed after three light stages in `42243934171`; its visibility-contract repair still needs native verification. No device-performance claim; dark cosmetics prose still needs a dedicated contrast review |
| Account-bound archive/Ghost/filter controls and disable-only reset | Core account facades passed local native cases; Settings catalog 8 and navigation-helper 10 cases passed at `c55e19c4`. Source routes use the selected `AccountContext`; the full app compiled | Actual account-bound dashboard callbacks, reset behavior and account switching in the running application remain unverified. Synthetic renderer callbacks do not verify those bindings |
| Browse retained archives with capture disabled; chat search and profile links | At `c55e19c4`, Core inline-history classes passed 17 cases and deleted-history classes passed 19; Settings gallery 4 and navigation-helper 10 cases passed. Fixtures exercise foreground read leases and separate local destinations | Authenticated chat search, profile navigation and gallery recordings remain unverified. These are separate local destinations, not merged server search or the canonical media grid |
| Bulk clear-history/date/thread and author/forward-author deletion capture | Production selected-row observer paths are source-wired; all 11 `LocalArchiveHistoryRemovalTests` and the 26-case replay class passed at `c55e19c4` using actual local Postbox helpers | No live two-client deletion claim. Handoff remains capped at 256 records / 8 MiB; overflow increments missed-capture count while canonical deletion proceeds. No extra full history scan; performance unmeasured |
| Reconnect/history-fetch replacements, validation removals and media-reference refreshes | At `c55e19c4`, remote reconciliation 7, server reconciliation 15 and feed reconciliation 4 cases passed, alongside capture/replay fixtures using production replacement hooks | Real reconnect/server behavior remains unverified. Same-second content uses persisted UUIDs and SQLite ordering; batch latency is unmeasured. Validation removal includes upstream RPC-error fallback and is not proof of remote sender deletion |
| Legacy group, broadcast and supergroup edit/delete replay | All 26 replay and 5 acknowledged-edit cases passed at `c55e19c4`, including locally invoked legacy-group, broadcast and supergroup replay | These production-path fixtures do not establish live Telegram delivery, sender deletion or another-client behavior |
| Deleted/Edited label preferences and current live-version preview | At `c55e19c4`, Core presentation 8, Settings presentation 8 and inline-status 3 cases passed; account-local editor/timeline/gallery bindings are source-wired and compiled | Actual account editor, live-version preview and conversation recordings remain unverified. The older 62546557 IPA does not verify the current inline marker |
| Encrypted import/export | Native picker, strict owner/environment binding, authenticated envelope, live-row reconstruction and transactional merge; all 24 library transfer cases and all 12 Core facade cases passed at `c55e19c4`, including seven pending-transfer authority/cancellation regressions | Local native transfer fixtures pass; real account picker/navigation remains unverified. Capture must be explicitly paused, with warned capture gap. Policy/lock/lease/disposal changes revoke pending transfers; authentication remains before destination creation. No AyuSync/network transport |
| Ordinary GIF, PDF and typed encrypted media playback | All 18 encrypted-playback cases passed at `c55e19c4`: 11 loader/player and 7 format cases, including PNG/JPEG, true GIF, PDF, AAC, Ogg/Opus PCM and H264/audio after encrypted reopen. Settings media-preview 8 cases passed | Capture fixtures separately preserve synthetic typed media bytes; decoder passes do not prove live incoming-media capture. Complete-app gallery recordings and physical-device playback remain unverified |
| Ordinary read/presence controls, story-opening warning, read after confirmed send | All 99 Core Ghost/story-warning cases passed at `c55e19c4`, exercising account policies and production workers with final typed RPC recorders | No live server, perfect-invisibility or other-client claim. Mandatory expiry behavior remains preserved; actual account controls and interaction scenarios need application verification |
| Text, bounded-expression, media-type and cached blocked-author filters | All 54 Core filter cases passed at `c55e19c4`, covering persisted preferences, bounded matchers and classification. The native rule editor is source-wired and compiled | The 25 text/filter Projection cases did not execute in either the original qualification or the later iPhone 15 Pro replay. Actual chat hide/reveal and editor recordings remain unverified; unknown metadata/albums/rich/secret/timed content stays visible |
| Explicit local sender hiding and inverse rules | [Account-local sender management and non-match predicates](fork/LOCAL-MESSAGE-FILTERS.md): all 15 Core sender/inverse cases passed at `c55e19c4` | Its 3 new presentation cases are among the 25 unexecuted text/filter Projection cases. Real chat/editor recordings remain pending. Unknown/empty/oversized/work-limited text stays visible to inverse text rules; no server block request or notification/preview filtering |
| Hide sponsored messages | All 5 `LocalPrivacyAdsTests` passed at `c55e19c4`, covering local consumer projection, restoration and account policy | Real sponsored-message display remains unverified. No Premium entitlement or server-request suppression; Telegram API-policy conflict remains documented |
| Local palette, motion preference and explicit bundled-icon permission | All 8 Core cosmetics cases passed at `c55e19c4`, including real preference reopen and entitlement boundaries. The byte-identical production renderer has the separately identified 8+1 synthetic UI evidence | Native OS icon switching, authenticated preferences UI and physical appearance remain unverified. No server Premium entitlement bypass |
| Local folder colors and tags | [Account-local tab/list projection and editor](fork/LOCAL-FOLDER-APPEARANCE.md): all 21 `LocalFolderAppearanceTests` passed at `c55e19c4`, covering real Postbox/schema/isolation and serialized-request invariance; the full app compiled | Actual account editor/tab recordings remain pending. Canonical requests/order/Premium limits stay unchanged. Removed IDs are pruned and stale editors rejected; byte-identical replacement without an observed removal is indistinguishable |
| Inline saved-edit history in text/caption bubbles | [Visible-cell marker and date action](AYUGRAM_INLINE_HISTORY.md): Core inline-history 17 cases, including 3 real Postbox integration cases, and Settings inline-status 3 cases passed at `c55e19c4`; app integration compiled | Actual conversation placement, date interaction and recordings remain unverified. Archive notifications invalidate committed deletion/pruning without manufacturing a Telegram message. Media-only/album status nodes remain pending; no physical-device or scrolling benchmark claim |
| Inline deleted-message cards | [Distinct local row and encrypted window query](fork/LOCAL-DELETED-ROWS-VERIFICATION.md): Core deleted-history 19 cases, including actual Postbox edit/delete/cache-cleanup/reopen integration, and relevant store cases passed at `c55e19c4` | The 12 archived-row projection and 4 real row-renderer cases did not execute in either the original qualification or the later iPhone 15 Pro replay. Full conversation placement/recordings remain unverified. At most forty excerpts in the loaded ordinary-chat window; no forum/thread/filter-enabled inline projection, shared date header or media thumbnail. Canonical reads, pagination, selection and server actions exclude local rows |
| Malformed archive/filter identity handling | [Safe persisted-peer boundary](fork/PEER-ID-BOUNDARIES.md): Core archive peer-ID 5 and Settings peer-reference 2 cases passed at `c55e19c4`, alongside filter identity/codec cases | Unknown imported identities remain locally browsable without native peer/message lookups; actual app navigation is unverified. Invalid outbox entries stay unacknowledged and incompatible preferences read-only |
| Timed, self-destructing and view-once retention | All 11 `LocalArchiveTimedMediaExclusionTests` passed at `c55e19c4`, proving deliberate exclusion and normal consumption/expiry behavior | **Retention is unsupported in this candidate.** These are exclusion tests, not preservation evidence. No misleading preservation switch or post-expiry recovery claim |

Historical qualification [42098718967](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42098718967)
targeted b24d8688 and exposed the build failures recorded above. The later c55
native passes and full builds now supply the bounded evidence listed here; its
original full qualification still failed at the unexecuted Projection gate.
Compilation and local native passes do not establish authenticated multi-client,
physical-device, low-storage or actual OS-kill behavior. Close/reopen database
fixtures and the synthetic renderer recording do not replace those checks.

Reference column abbreviates the pinned public sources documented in the
[Android](fork/AYUGRAM-REFERENCE.md), [Desktop](fork/DESKTOP-REFERENCE.md) and
[November audit](fork/REFERENCE-AUDIT-2026-11-05.md). “Native-tested” describes the
named local tests; “source-wired” means a real call path exists but does not by
itself establish the authenticated running-app experience.

| Feature | Android / Desktop implementation or reference | Native iOS equivalent / code boundary | Baseline status and limitation |
| --- | --- | --- | --- |
| Deleted text in private chats, groups and channels | Android `AyuMessagesController`; Desktop `processMessageDelete` | `LocalArchiveCapture`, durable Postbox outbox, account worker and SQLCipher store | Native local-pipeline tests pass; opt-in, available cloud content only; live sender-deletion scenario unverified |
| Edits and all observed previous versions | Android `MessagesStorage`/edited DAO; Desktop edition hook | Capture before server replay/own-edit acknowledgement; version timeline | Native local integration passes; unsupported rich content and unseen history are not recovered |
| Deletion/edit timestamps | Both reference databases record revisions/deletion metadata | Observed deletion time and server edit time stored separately | Native storage/integration tests pass; observation time is not proof of server deletion time |
| Photos | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Generated JPEG exercised through local capture/cache cleanup; source supports completed cloud images |
| Videos | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Typed local byte-capture fixtures and separate encrypted H264/audio decoding cases pass; live incoming-video capture unverified |
| GIFs/animations | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Classified separately as animation; byte-path fixtures do not prove every GIF decoder |
| Voice messages | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Voice metadata and completed cached bytes supported; live voice playback/capture unverified |
| Round/video messages | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Separate videoMessage classification; real view-once round/voice content excluded |
| Documents | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Streaming file integration includes a 20 MiB synthetic document; live received-document workflow unverified |
| Incomplete or missing downloads | Android may request downloads before/after deletion | Store descriptive metadata and report unavailable bytes; never claim preservation from metadata alone | Partial: only complete locally cached eligible resources; no promised redownload after deletion |
| Independent media cache | Android separate attachment folder; Desktop DB plus memory | Archive-owned encrypted media outside MediaBox cache | Native local cache-clear/reopen tests pass; physical cache-clear scenario still unverified |
| Crash-safe handoff and replay | Neither reference DB alone proves cross-store atomicity | Durable ordered Postbox capture operations and idempotent archive receipts | Native local rollback/replay tests pass; two SQLite stores are not one atomic commit |
| Account isolation | Android user/dialog keys; Desktop user/dialog/topic keys | Account directory, Keychain identity and archive DB identity | Native isolation tests pass; SideStore/team/profile migration and physical lock still unverified |
| Retention age and storage limits | Android cleanup notes; no current public implementation | Transactional retention with bounded payload budget and native presets | Native tests pass; payload accounting is not full physical-disk quota; baseline default 32 MiB |
| Chat/group/channel exclusions | October Android release claim; historical Java bot gate | Persistent excludedPeerIds policy before capture | Policy is implemented/tested; baseline has only chat-specific control, broad picker pending |
| Sender/user exclusions | Required product behavior; modern Android internals unavailable | Persistent excludedSenderIds; fail closed when sender unknown | Core policy implemented/tested; baseline scope-management UI incomplete |
| Bot exclusions | Android saveForBots; Desktop saveForBots | Account-local excludeBots policy | Native tests pass; baseline default excludes bots |
| Deleted-only screen and search | Android View Deleted; Desktop history section | Native paged archive screen and encrypted indexed local queries | Source-wired/compiled; local tests pass; authenticated UI recordings pending |
| History timeline | Android AyuMessageHistory; Desktop history UI | Native original/edited/deleted version screen | Source-wired; real conversation navigation and recorded UI pending |
| Media gallery | October Android profile-media claim; Desktop persistent media absent | Native encrypted gallery with type filters and per-chat queries | Source-wired; gallery helper tests pass; actual authenticated gallery UI unverified |
| Archive management and explicit deletion | Reference dialog clearing; Android DB tools | Native usage counters, retention controls and generation-aware purge | Core tests pass; exact physical allocation/cryptographic erasure not claimed |
| Deleted hits in standard chat search | October Android release claim | Merge explicitly local archive hits without uploading archived text/index | Absent in baseline |
| Deleted media in standard chat profile | October Android release claim | Explicit archive media route/tab scoped to the current chat | Standalone chat-scoped gallery exists; standard profile integration absent in baseline |
| Inline deleted/edited indicators | Android customizable marks; Desktop semi-transparent/mark rendering | Native stable archive projection and tap-to-history behavior | Absent in baseline; no reconstruction into outgoing/server message queues |
| Custom deleted/edited labels | Android mark preferences; Desktop deletedMark/editedMark | Bounded local marker preference and renderer | Absent in baseline |
| Per-chat archive clearing | Desktop clear dialog/topic; Android database controls | Account/chat-scoped explicit purge with replay generation safety | Store/coordinator controls exist; per-chat UI completeness unverified |
| Account removal and logout retention | References differ; no general permanence proof | Keep policy explicit and separate from ordinary media-cache clearing | Not guaranteed: upstream account-directory cleanup can remove the contained archive |
| History after removal from a group | Android cache retention claim; Desktop DeletedDialog schema incomplete | Retain already archived eligible content only | Previously archived data remains while account directory exists; full removed-group history recovery absent |
| Encrypted import/export | October Android import/export options; encryption not established | New versioned authenticated local-file format with bounded parsing and account mapping | Absent in baseline; no plaintext fallback or export of Telegram sessions |
| Optional encrypted synchronization | Historical Android AyuSync transport; Desktop AyuSync name is forwarding helper | Separate protocol/security review, explicit user enablement, local-only default | Not implemented: no reviewed compatible E2E archive protocol/backend source |
| Timed/self-destructing photos | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Timed/self-destructing videos | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| View-once photos | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| View-once videos | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Disappearing voice messages | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Disappearing round/video messages | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Cloud auto-delete timer messages | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Secret Chat retention | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Message read suppression | Historical Android request interception; Desktop typed history/content gates | Native operation policy preserving local state and canonical queues | Absent in baseline; API-contract conflict; new candidate requires its own tests |
| Story view suppression | Current docs and Desktop story gates | Account-scoped producer/queue generation checks in LocalStoryPrivacy | 26 native local story tests passed in625; reactions/replies and other clients can reveal views |
| Typing suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Sticker-selection suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Voice/round recording suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Upload progress suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Game activity suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Online suppression | Android updateStatus rewrite; Desktop presence gate | Gate explicit native presence requests without changing transport/authentication | Absent in baseline; API-contract conflict; sending/reactions/other clients may still reveal activity |
| Automatic offline attempt | Desktop worker timer; Android preference | Bounded explicit offline operation after unavoidable activity | Absent in baseline; no guaranteed background timer or invisibility |
| Read on interaction | Android read after send; Desktop multiple explicit actions | Scoped, documented one-shot permission after chosen user actions | Absent in baseline; intentionally reveals reading when enabled |
| Ghost scheduled sending | Docs/ Desktop media-dependent delayed scheduling | Reuse real Telegram scheduling; preserve explicit schedules and failures | Absent; reference documents unreliability; no hidden fallback or invisibility claim |
| Warning before opening stories | Android docs; Desktop window controller | Cancelable pre-navigation warning before receipt-producing story work | Absent in baseline |
| Per-account Ghost preferences | Desktop account/global settings; old Android global SharedPreferences | Postbox-backed account activity/story preferences | Implemented locally; native tests pass; full privacy-mode state not implemented |
| Quick Ghost toggle and active status | Android drawer; Desktop drawer/tray/context controls | Visible aggregate account control with exact protection list | Baseline lists activity/story state in nested settings; chat quick toggle absent |
| Global/per-chat literal filters | References use regex, which also supports plain text | Bounded literal evaluator and per-account persisted rules | 18 native core and13 native projection tests passed; incoming ordinary text scope only |
| Case-sensitive/insensitive matching | Android regex flags; Desktop flags | Deterministic literal case choice | Implemented/tested for literal rules; not equivalent to all Java regex behavior |
| Global-filter exclusions | Android shared exclusions; Desktop DB exclusions | Skip globals in selected peers while keeping explicit chat rules | Core implemented/tested; broad scope picker incomplete in baseline |
| Create rule from a message | Android selected text; Desktop context edit filter | Native rule editor prefilled with message excerpt | Source-wired; not proof of precise selected-substring behavior or authenticated UI |
| Temporary reveal | Desktop revealed-dialog set | Account/chat-local presentation reveal with clear state | Source-wired/tested projection path; UI frame verification pending |
| Regular expressions | Android Java regex; Desktop ICU regex | Bounded supported syntax or independently reviewed engine, never arbitrary main-thread backtracking | Absent in baseline; no misleading regex toggle |
| Media/button-type filters | References append metadata tags to regex input | Structured local predicates over media/button metadata | Absent in baseline |
| Hide blocked users locally | Android docs; Desktop sender/via-bot/forwarded checks | Explicit local presentation rule distinct from server block state | Absent in baseline |
| Local shadow bans | Desktop shadowBanIds | Account-local sender exclusions that do not alter server content | Absent as a display filter; archive sender-exclusion policy is a different feature |
| Inverse matching, albums and previews | Desktop reversed filter and Android album/dialog behavior | Define group semantics, ordinary previews, notifications and reveal | Baseline excludes media/forwarded/grouped/large emoji/rich/secret/expiring messages |
| Remote filter import and paste export | Android allowlisted hosts; Desktop dpaste publishing | Prefer explicit local files; network import needs separate validation/consent | Not implemented; no background rules download or archive paste upload |
| Sponsored-message hiding | Android disableAds; Desktop disableAds | Local display policy, separate from transport and telemetry | Absent in baseline; API terms §3.3 conflict must be disclosed if implemented |
| Proxy sponsor/promotional elements | Android docs; Desktop advertising controls | Local presentation only, without changing proxy authentication/networking | Absent in baseline |
| Local Premium cosmetics | Docs distinguish client visuals from server limits | Independent appearance preferences; never fake paid server entitlement | Absent as an AyuGram feature in baseline; ordinary upstream appearance remains |
| Local folder colors/tags | October Android claim; Desktop uses server tag API in inspected path | Account-local presentation metadata only | Absent; do not use messages.toggleDialogFilterTags as proof of local-only behavior |
| Emoji/profile/reply colors and icons | Android documentation | Local visual overlays only where renderers support them | Absent as local overrides; server Premium settings remain entitlement-gated |
| Native light/dark themes and text scaling | References appearance; upstream iOS already supplies native controls | Reuse native theme/accessibility settings | Upstream source exists; not proof of newly customized AyuGram UI |
| Alternative app icons | Desktop picker; Android exteraGram behavior | Own bundled artwork and native alternate-icon API | Upstream icon code exists; new AyuGram artwork/eligibility controls absent in baseline |
| Prominent searchable Custome options | New user requirement; Desktop searchable builder; October Android redesign | One native top-level dashboard with 11 categories and real account controls | Absent in shipped baseline; active implementation is not yet validated |
| Settings reset | Reference preferences resets | Explicit category/account reset without deleting archive content unless separately confirmed | Only individual existing policy operations; combined dashboard reset absent in baseline |
| Restricted forwarding / AyuForward | Android download/reupload; Desktop forward reconstruction | Respect copy protection and server permissions; normal forwarding stays upstream | No bypass; copy-protected archive capture excluded; no automatic archive upload |
| Peek online / temporary privacy edits | Android docs modifies exceptions and may use another account | No automatic account privacy mutation or secret cross-account lookup | Not implemented; may expose own presence and requires many server requests |
| Screenshots / app-switcher / streamer mode | Android FLAG_SECURE; Desktop capture exclusion | Only public iOS capture-state/app-switcher redaction controls where available | Desktop/Android mechanisms not portable; no platform-security bypass or perfect screenshot prevention |
| Message shots | Desktop selection screenshot editor | Native explicitly requested rendered export, with privacy review | Not implemented; archive views have no implicit share/export pipeline |
| Own API identity and telemetry removal | Reference advertises official keys and Firebase; Desktop crash setting | Own credentials from Secrets; existing reviewed containment; no archive networking | Source containment implemented; runtime zero-telemetry/privacy audit remains unverified |
| Performance and iPhone15 Pro | No relevant native benchmark supplied by references | Measure scroll frames, startup, memory, disk, battery and async work | No physical iPhone benchmark; host/unit timing is not a device performance claim |

## Native source map and advertised-control rule

- Archive policy/capture: `TelegramCore/Sources/LocalArchive/LocalArchiveSettings.swift`,
  `LocalArchiveCapture.swift`, `LocalArchiveCaptureOperation.swift`,
  `LocalArchiveCoordinator.swift`; encrypted store: `LocalMessageArchive`; native
  screens: `SettingsUI/Sources/LocalArchiveSettingsController.swift`.
- Activity/story privacy: `TelegramCore/Sources/LocalPrivacy/LocalGhostSettings.swift`,
  `LocalGhostContext.swift` and the story-privacy implementation; controller:
  `SettingsUI/Sources/LocalGhostSettingsController.swift`.
- Filters: `TelegramCore/Sources/LocalMessageFilter/` and
  `ChatHistoryEntry/ChatLocalTextFilterProjection`; controller:
  `SettingsUI/Sources/LocalMessageFilterSettingsController.swift`.
- Baseline entry points are nested in Data and Storage and chat-specific routes;
  archive/activity/filter enablement defaults off. The requested prominent
  `Custome options` dashboard is an explicit new requirement, not something
  established by existing library tests or the Welcome screen.

Every newly advertised control must name its persistent owner, behavior call
site and actual validation. An unsupported category may explain its limitation
but must not expose a switch that saves a value with no effect. Retention defaults,
account scope and reset behavior must be visible. No server Premium entitlement,
expired content access or third-party archive upload may be fabricated.

## Complete public Desktop preference inventory

The following **109 distinct fields** are defined in the pinned
[`ayu_settings.h`](https://github.com/AyuGram/AyuGramDesktop/blob/0f2a31bb420de98f331990432265b131b8d836b5/Telegram/SourceFiles/ayu/ayu_settings.h).
Reactive `Value`/`Changes` accessors and raw cloud-theme credential/ID fields are
excluded as implementation duplicates, not omitted user features. Definitions
alone are not Desktop runtime verification. Composite filter records, shadow-ban
sets, message-shot cloud themes, translators and forwarding are covered above.
None of these flags should be counted as implemented iOS features merely because
a corresponding settings row can be drawn.

| Desktop option / source line | Android reference correspondence | Proposed native iOS equivalent | Baseline iOS status |
| --- | --- | --- | --- |
| `sendReadMessages` (89) | sendReadPackets | Native read operation gates | Absent |
| `sendReadStories` (90) | Not established in public historical Android; docs/release may differ | Account story operation gates | 26 native local story cases pass |
| `sendOnlinePackets` (91) | sendOnlinePackets | Explicit presence gate | Absent |
| `sendUploadProgress` (92) | sendUploadProgress | Typed activity-category policy | 15 native local activity cases cover categories |
| `sendOfflinePacketAfterOnline` (93) | sendOfflinePacketAfterOnline | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `markReadAfterAction` (94) | markReadAfterSend (narrower) | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `useScheduledMessages` (95) | useScheduledMessages | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `sendWithoutSound` (96) | Not established in public historical Android; docs/release may differ | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `suggestGhostModeBeforeViewingStory` (98) | Not established in public historical Android; docs/release may differ | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `isGhostModeActive` (99) | Not established in public historical Android; docs/release may differ | Display exact active capabilities | Activity/story status only; full mode absent |
| `sendReadMessagesLocked` (102) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `sendReadStoriesLocked` (103) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `sendOnlinePacketsLocked` (104) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `sendUploadProgressLocked` (105) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `sendOfflinePacketAfterOnlineLocked` (106) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `showBackground` (186) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `showDate` (187) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `showReactions` (188) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `showHeaderDecorations` (189) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `showColorfulReplies` (190) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `revealSpoilers` (191) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `embeddedThemeType` (192) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `useGlobalGhostMode` (257) | Not established in public historical Android; docs/release may differ | Prefer independent account policy | Per-account activity/story controls; global aggregation absent |
| `saveDeletedMessages` (270) | saveDeletedMessages | Account archive policy/outbox | Local integration tested; live app proof pending |
| `saveMessagesHistory` (271) | saveMessagesHistory | Pre-overwrite capture/timeline | Local integration tested; UI proof pending |
| `saveForBots` (272) | saveForBots | Account excludeBots | Implemented/tested; default excludes bots |
| `filtersEnabled` (273) | regexFiltersEnabled | Account local filter master switch | Core/projection tested; limited incoming text |
| `filtersEnabledInChats` (274) | regexFiltersInChats | Scoped rules and exclusions | Implemented/tested for literals |
| `hideFromBlocked` (275) | Not established in public historical Android; docs/release may differ | Account-local sender presentation predicate | Absent |
| `semiTransparentDeletedMessages` (276) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableAds` (277) | disableAds | Explicit local sponsor presentation policy | Absent; API terms §3.3 conflict |
| `disableStories` (278) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableCustomBackgrounds` (279) | Not established in public historical Android; docs/release may differ | Native wallpaper selection/disable | Upstream settings exist; Ayu-specific override absent |
| `hidePremiumStatuses` (280) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showOnlyAddedEmojisAndStickers` (281) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `collapseSimilarChannels` (282) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `hideSimilarChannels` (283) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `messageBubbleRadius` (284) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableOpenLinkWarning` (285) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `wideMultiplier` (286) | Not established in public historical Android; docs/release may differ | Native iPhone/iPad/webview layout, platform-specific | Desktop geometry is not portable; custom override absent |
| `spoofWebviewAsAndroid` (287) | Not established in public historical Android; docs/release may differ | Preserve truthful native webview identity | Not copied |
| `increaseWebviewHeight` (288) | Not established in public historical Android; docs/release may differ | Native iPhone/iPad/webview layout, platform-specific | Desktop geometry is not portable; custom override absent |
| `increaseWebviewWidth` (289) | Not established in public historical Android; docs/release may differ | Native iPhone/iPad/webview layout, platform-specific | Desktop geometry is not portable; custom override absent |
| `materialSwitches` (290) | Not established in public historical Android; docs/release may differ | Native themed switches | Upstream UIKit equivalent, not Material3 port |
| `removeMessageTail` (291) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableNotificationsDelay` (292) | Not established in public historical Android; docs/release may differ | Review native notification/OS limits | Not portable as a guaranteed background effect |
| `localPremium` (293) | localPremium | Independent local cosmetics only | Absent; no server entitlement changes |
| `showChannelReactions` (294) | Not established in public historical Android; docs/release may differ | Local reaction presentation only | No Ayu override; server entitlement/permissions preserved |
| `showGroupReactions` (295) | Not established in public historical Android; docs/release may differ | Local reaction presentation only | No Ayu override; server entitlement/permissions preserved |
| `showPrivateChatReactions` (296) | Not established in public historical Android; docs/release may differ | Local reaction presentation only | No Ayu override; server entitlement/permissions preserved |
| `appIcon` (297) | Not established in public historical Android; docs/release may differ | Own bundled alternate icons | Upstream native API/source; Ayu artwork absent |
| `simpleQuotesAndReplies` (298) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `hideFastShare` (299) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `replaceBottomInfoWithIcons` (300) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `deletedMark` (301) | deletedMarkText | Bounded local marker/rendering | Absent |
| `editedMark` (302) | editedMarkText | Bounded local marker/rendering | Absent |
| `unlimitedRecentStickers` (303) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showReactionsPanelInContextMenu` (304) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showViewsPanelInContextMenu` (305) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showHideMessageInContextMenu` (306) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showUserMessagesInContextMenu` (307) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showMessageDetailsInContextMenu` (308) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showRepeatMessageInContextMenu` (309) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showAddFilterInContextMenu` (310) | Not established in public historical Android; docs/release may differ | Native local rule editor route | Message excerpt route exists; exact selection/UI unverified |
| `showAttachButtonInMessageField` (311) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showCommandsButtonInMessageField` (312) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showEmojiButtonInMessageField` (313) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showMicrophoneButtonInMessageField` (314) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showAutoDeleteButtonInMessageField` (315) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showGiftButtonInMessageField` (316) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showAiEditorButtonInMessageField` (317) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showAttachPopup` (318) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showEmojiPopup` (319) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showMyProfileInDrawer` (320) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showBotsInDrawer` (321) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showNewGroupInDrawer` (322) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showNewChannelInDrawer` (323) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showContactsInDrawer` (324) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showCallsInDrawer` (325) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showSavedMessagesInDrawer` (326) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showLReadToggleInDrawer` (327) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showSReadToggleInDrawer` (328) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showNightModeToggleInDrawer` (329) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showGhostToggleInDrawer` (330) | showGhostToggleInDrawer | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showStreamerToggleInDrawer` (331) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showGhostToggleInTray` (332) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showStreamerToggleInTray` (333) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `monoFont` (334) | Not established in public historical Android; docs/release may differ | Explicit local code-font option | Absent as Ayu customization |
| `hideNotificationCounters` (335) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `hideNotificationBadge` (336) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `hideAllChatsFolder` (337) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `channelBottomButton` (338) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `quickAdminShortcuts` (339) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableGreetingSticker` (340) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showPeerId` (341) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showMessageSeconds` (342) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showMessageShot` (343) | Not established in public historical Android; docs/release may differ | User-requested native rendered export | Absent |
| `filterZalgo` (344) | Not established in public historical Android; docs/release may differ | Bounded Unicode presentation sanitizer | Absent |
| `stickerConfirmation` (345) | Not established in public historical Android; docs/release may differ | Explicit native pre-send confirmation | Absent as Ayu preference |
| `gifConfirmation` (346) | Not established in public historical Android; docs/release may differ | Explicit native pre-send confirmation | Absent as Ayu preference |
| `voiceConfirmation` (347) | Not established in public historical Android; docs/release may differ | Explicit native pre-send confirmation | Absent as Ayu preference |
| `roundConfirmation` (348) | Not established in public historical Android; docs/release may differ | Explicit native pre-send confirmation | Absent as Ayu preference |
| `translationProvider` (349) | Not established in public historical Android; docs/release may differ | Upstream native translation; optional provider review | No new third-party provider imported |
| `adaptiveCoverColor` (350) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `improveLinkPreviews` (351) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `crashReporting` (352) | Not established in public historical Android; docs/release may differ | No added collector/upload toggle | Reviewed containment exists; runtime audit pending |
| `avatarCorners` (353) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `singleCornerRadius` (354) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `streamerMode` (355) | Not established in public historical Android; docs/release may differ | Public capture-state redaction where supported | Absent; no perfect iOS screenshot-prevention guarantee |

## Historical Android configuration inventory

These **28 persisted boolean fields** come from
[`AyuConfig.java`](https://github.com/AyuGram/AyuGram4A/blob/7013145676d36d82ee13c02a89f72097b7490dcd/TMessagesProj/src/main/java/com/radolyn/ayugram/AyuConfig.java).
They are not a source listing for build 20261104. String preferences additionally
include deleted/edited markers, regex patterns and AyuSync URL/token; those are
covered explicitly above. No token or key value is copied here.

| Historical Android field | iOS equivalent / exclusion |
| --- | --- |
| `sendReadPackets` | Native typed account privacy policy; see the matching core feature and its limits |
| `sendOnlinePackets` | Native typed account privacy policy; see the matching core feature and its limits |
| `sendOfflinePacketAfterOnline` | Native typed account privacy policy; see the matching core feature and its limits |
| `sendUploadProgress` | Native typed account privacy policy; see the matching core feature and its limits |
| `useScheduledMessages` | Native typed account privacy policy; see the matching core feature and its limits |
| `saveDeletedMessages` | Account archive policy and capture pipeline; exact controls described above |
| `saveMessagesHistory` | Account archive policy and capture pipeline; exact controls described above |
| `saveMedia` | Independent encrypted completed-media retention, eligible cloud resources only |
| `saveMediaInPrivateChats` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveMediaInPublicChannels` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveMediaInPrivateChannels` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveMediaInPublicGroups` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveMediaInPrivateGroups` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveFormatting` | Flat text entities retained; unsupported structured rich content reported as missed |
| `saveReactions` | No general reaction-history archive parity established |
| `saveForBots` | Account-local excludeBots (inverted semantics) |
| `markReadAfterSend` | Native typed account privacy policy; see the matching core feature and its limits |
| `keepAliveService` | Android service/process UI is not an iOS feature; respect background execution |
| `disableAds` | Local display policy only; API terms §3.3 conflict; baseline absent |
| `localPremium` | Local cosmetics only; no server Premium entitlement |
| `regexFiltersEnabled` | Current baseline supports bounded literals, not Java regex compatibility |
| `regexFiltersInChats` | Current baseline supports bounded literals, not Java regex compatibility |
| `regexFiltersCaseInsensitive` | Current baseline supports bounded literals, not Java regex compatibility |
| `showGhostToggleInDrawer` | Native chat/settings quick control required; no Android drawer copied |
| `showKillButtonInDrawer` | Android service/process UI is not an iOS feature; respect background execution |
| `syncEnabled` | No unreviewed AyuSync transport; local-only storage |
| `useSecureConnection` | No unreviewed AyuSync transport; local-only storage |
| `WALMode` | Reviewed archive SQLCipher/WAL policy, not an unverified debug switch |

## Other documented features, platform boundaries and release delta

| Public reference behavior | Native equivalent / baseline status |
| --- | --- |
| Ayu settings/archive/filter/import deep links | Route only to real supported native screens; unavailable in baseline. No unauthenticated arbitrary remote import. |
| Open profile by numeric ID | Use existing peer/access-hash privacy rules; no invented server access; dedicated Ayu route absent. |
| Register URL scheme | Preserve iOS bundle/link configuration and user choice; desktop OS registration mechanism is inapplicable. |
| Search-bar last-seen widget | Local UI using already allowed status; no inferred/private presence access; absent. |
| Show content in app switcher | Native lifecycle redaction where public APIs allow it; Android FLAG_SECURE behavior is not portable. |
| Debug download buttons, alert reset and statistics | Developer-only diagnostics; not production feature parity or verification. |
| Android FCM/keepalive/vendor FPS behavior | Use valid Apple capabilities and iOS constraints; no Android identity spoofing, permanent daemon or promised refresh rate. |
| New November release capabilities | All19 delegated release groups and native proposals are individually listed in the [dated November audit](fork/REFERENCE-AUDIT-2026-11-05.md#november-2026-release-delta-and-native-equivalents). Current Android corresponding source remains unavailable. |

## Security, licensing and acceptance boundaries

Use the owner's API credentials from GitHub Secrets. Keep Telegram authentication,
encryption and transport architecture; no custom proxies, session uploads,
developer archive service or unverified runtime-code loader. Retain exact GPL
corresponding source/build scripts/notices and distinct app branding. See
[license and supply-chain audit](fork/LICENSE-AND-SUPPLY-CHAIN.md),
[security assessment](SECURITY_ASSESSMENT.md), [preservation limits](fork/PRESERVATION-STATUS.md),
[Windows installation](WINDOWS_INSTALL.md) and [SideStore evidence](SIDESTORE_INSTALL.md).

API terms §1.4 prohibits preventing self-destructing content from disappearing and
altering read/online/typing behavior; §3.3 addresses sponsored messages. An opt-in
switch does not establish a contractual exception. Keep timed/view-once retention
unsupported unless a real permissible scope is identified; explain conflicts for
experimental controls rather than falsely claiming compliance. Rooted/jailbroken
access, encryption bypass and server-side restriction bypass are excluded.

The independent archive survives normal cache clearing, not arbitrary parent
account-directory removal. Keychain/profile changes, account migration, physical
storage/locked-device behavior, interrupted upgrades and actual logged-in media
capture remain acceptance gates. Visual changes require recordings and frame
review; compilation and unit tests alone do not establish their appearance.
