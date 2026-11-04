# Windows build download and iPhone installation

No full-application build or device IPA has succeeded yet. Downloadable test
evidence exists, including passing iOS simulator integration suites, but those
ZIP files do not contain an installable application. These instructions describe
how to obtain and test a device build after the application workflow succeeds.

Use **Archive application integration (hosted macOS)** for the archive,
Ghost activity, story-view privacy and literal-filter branches. Pushes run it automatically;
`ci/parallel-device-build` also runs it to verify the build-job split. The
`integration-tests` job must pass for the exact commit before application jobs
become eligible to start. The `application-build` simulator job and the
`unsigned-device-build` job then run independently, subject to runner capacity.
Each has its own 360-minute limit and branch-specific queue. A slow simulator
build no longer consumes the device build's time budget or prevents it starting.
The device job uses Xcode's device SDK and does not download a simulator runtime.

The existing simulator concurrency group is retained with
`cancel-in-progress: false`; the new device queue also preserves running jobs.
Publishing this workflow change does not alter jobs already in progress. GitHub
can replace an older *pending* job when a newer commit enters the same queue, so
always inspect the commit of the job that actually ran. No private Bazel state
or credentials are shared between jobs; some compilation repeats on fresh hosts.

| Result | Downloadable artifact | What it establishes |
| --- | --- | --- |
| iOS integration tests | `archive-integration-tests-<commit>` | Test logs and checksums, not an app package. Available before full application compilation finishes. |
| Simulator application compilation | `archive-integration-<commit>` | On success, `fork-baseline-simulator/Telegram.ipa` and its manifest/checksum. This package is for the simulator and cannot run on a physical iPhone. |
| Unsigned device compilation | `archive-unsigned-device-<commit>` | On success, `fork-baseline-unsigned-device/Telegram.ipa` and its manifest/checksum. It still requires independent signing before installation. |
| Windows signing and physical installation | Produced locally by your chosen signing tool | A different, signed package. Installation, entitlements and runtime behavior must be verified separately on the iPhone. |

Match every artifact's commit to the intended PR head. Failed application jobs
can still upload diagnostics without a package; a downloadable ZIP alone does
not establish build success. The separate **Archive engine draft tests** workflow
publishes library test evidence only. Earlier **Fork baseline (hosted macOS)**
runs retain their `fork-baseline-<commit>` artifact name and their original job
layout. A successful run of one branch does not validate another branch.

1. Open the authorized fork `Livara-Dev/Telegram-iOS`. In Settings →
   Secrets and variables → Actions, set repository secrets `TELEGRAM_API_ID` and
   `TELEGRAM_API_HASH` from your own application registered at
   https://my.telegram.org/apps. The workflow uses `org.livara.ayugram`, as requested;
   its availability has not been verified in Apple's developer portal. Use your
   own `APPLE_TEAM_ID` for signed builds. Never copy Telegram's identity.
2. Open Actions → **Archive application integration (hosted macOS)** and select
   the latest push run for the intended branch/commit. Manual **Run workflow**
   becomes available after the workflow exists on the default branch. Its
   `unsigned_device` option controls the independent device job; pushes enable
   that job automatically. All jobs use GitHub's `xcode-27` hosted Apple Silicon
   image, verify Xcode 27.1/macOS 27 and the pinned Bazel download. Test and
   simulator jobs validate the installed iOS 27.0 runtime; the device job does not.
3. After `unsigned-device-build` succeeds, download
   `archive-unsigned-device-<commit>`. Open its
   `fork-baseline-unsigned-device/manifest.json`; confirm `source_commit` and
   `mode: unsigned-device`. `signing: disabled` and unverified signature/runtime
   fields are expected at this stage. The device manifest does not claim that
   tests ran in the device job: inspect the same run's successful
   `integration-tests` job and its separate evidence artifact.
4. Compare the IPA to its SHA-256 checksum in Windows PowerShell:
   `Get-FileHash .\Telegram.ipa -Algorithm SHA256`, from the extracted
   `fork-baseline-unsigned-device` directory. Compare with its `SHA256SUMS` and
   manifest. Select the **device** output; a simulator IPA cannot run on an iPhone,
   even if both report arm64. Build logs have their own `SHA256SUMS` under
   `fork-evidence`.
5. For local signing, obtain Sideloadly only from https://sideloadly.io/ and follow
   its current Windows requirements for Apple device drivers/iTunes/iCloud.
   Connect and trust the iPhone via USB, select the device IPA, and sign with your
   own Apple account. Enter Apple credentials only into the signing software you
   have chosen to trust, never into this repository, workflow logs or chat.
6. Follow Apple's trust and Developer Mode prompts if required. A free Apple
   development account generally requires re-signing after seven days and has
   app/device limits; check current Apple/Sideloadly rules. An unsigned IPA is
   not independently signed until this process actually succeeds.

Sideloadly re-signing is **unverified for this fork**. Telegram includes several
extensions and a watch app and depends on matching App Groups, keychain groups
and profiles. Free-account signing or extension removal can break notifications,
sharing, widgets, watch support or startup. Do not describe a stripped/re-signed
package as equivalent to the tested artifact. It has a new hash and needs its own
signature, entitlement, installation and runtime validation. A paid developer
identity with correctly provisioned identifiers is the most direct route to a
fully independently signed build, but this has not been demonstrated here.

No jailbreak, MDM enrollment or disabling iOS security is part of the procedure.
The baseline still contains upstream telemetry. Archive, activity-suppression
and literal-filter tests are not evidence of complete application behavior or
physical-device installation. Full Ghost Mode and advanced filtering remain
unfinished. Do not use sensitive accounts for validation until the documented
privacy/security gates pass.
