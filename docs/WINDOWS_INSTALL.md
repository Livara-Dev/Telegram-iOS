# Windows build download and iPhone installation

An unsigned ARM64 device build succeeded for commit
`9a040579b79b2a055c3d83e2d1e703af8362c07d` in
[run 41896517472](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41896517472).
Download the [device artifact](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41896517472/artifacts/13361534875)
and extract `fork-baseline-unsigned-device/Telegram.ipa`. The IPA SHA-256 is
`192ad471031a6d274cc9740448ea54c0f58501c3dc3007e2c47f132341e92e69`.
The artifact ZIP has a different checksum. The package's checksums, bundle ID
and ARM64 iPhoneOS platform were independently checked; it contains no embedded
provisioning profiles or code signatures. It has not been installed or run on a
physical iPhone. Native integration qualification is separate from this successful
device compilation; consult that run's `integration-tests` result before use.
GitHub retains this artifact until November 11, 2026, unless removed earlier.

Use **Archive application integration (hosted macOS)** on the single
`ci/verify-latest` verification branch. Feature/PR commits no longer start three
redundant macOS workflows. Advance that branch once a source batch is ready;
one global qualification queue preserves its active run and retains only the
latest pending successor. Existing submitted jobs keep their original settings.
See [queue policy and observed capacity failure](fork/CI-QUEUE.md).

The integration and unsigned-device jobs use the same commit and may execute
alongside each other. `application-build` requires successful integration tests.
The device build has no simulator dependency; its output still needs same-commit
qualification. Manual standalone workflows are diagnostics, not automatic
second builds. A temporary, hash-verified unsigned rules overlay omits profile
embedding for the unsigned build only. No signing credentials are synthesized.

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
   the latest verification run for the intended commit. Manual **Run workflow**
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
   tests ran in the device job: inspect the same run's
   `integration-tests` job and its separate evidence artifact. The device manifest
   explicitly records `integration_qualification: not_evaluated_by_this_job`;
   a device build can exist even when simulator tests failed. It is not a release
   qualification or a functional-app claim.
4. Compare the IPA to its SHA-256 checksum in Windows PowerShell:
   `Get-FileHash .\Telegram.ipa -Algorithm SHA256`, from the extracted
   `fork-baseline-unsigned-device` directory. Compare with its `SHA256SUMS` and
   manifest. Before publication, the workflow checks the bundle ID, declared SDK
   and ARM64 Mach-O platform of the main executable. This is not signature or
   runtime verification. Select the **device** output; a simulator IPA cannot run on an iPhone,
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
Reviewed automatic telemetry collectors and upload sinks have been disabled;
see [the containment scope and remaining review](fork/TELEMETRY-CONTAINMENT.md).
This is not proof of zero telemetry or external connections. Archive,
activity-suppression and literal-filter tests are not evidence of complete
application behavior or physical-device installation. Full Ghost Mode and
advanced filtering remain unfinished. Do not use sensitive accounts for
validation until the documented privacy/security gates pass.
