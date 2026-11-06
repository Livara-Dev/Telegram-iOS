# Windows build download and iPhone installation

The current SideStore candidate is the **main-app-only unsigned ARM64 device IPA**
from [run 42204637743](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743),
[artifact 13495848146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743/artifacts/13495848146).
Extract `AyuGram-c55e19c4-main-app-only-unsigned-device.ipa`. Its IPA SHA-256 is:

`2cb541e76eb632b5b66c24a5e1937b6042cc230551c418948d75291f69e45cc8`

It retains application source `c55e19c4f5cbe0d730499035d0a0ef712f6903a9`, device
build **82** and bundle `org.livara.ayugram`. Exactly six extensions were removed;
all 880 retained ZIP entries match the original device package's bytes and
metadata. All six remaining images are unsigned ARM64 iPhoneOS; five frameworks
and all nineteen bundled dependency edges were verified. The artifact includes
checksums and the three exact-source/GPLv2 materials. It needs valid signing.

The matching retained main-app-only simulator application reached the actual
AyuGram authorization screen twice on a fresh **iPhone 15 Pro / iOS 27.0** device.
Both screenshots and the complete 390-frame variable-rate recording were
visually reviewed. The app created its private account-store directory and kept
the same identity after restart. Private storage was explicitly selected before
launch; this does **not** prove automatic fallback, Telegram login, authenticated
Settings navigation, SideStore signing, iOS 27.2 behavior or a physical-device
black-screen repair. No Telegram login codes or existing account session material
were used during replay. The app retains its registered API configuration.

The device and simulator retain separate origins: device82 from `42160468600`,
simulator1 from `42174474046`, at the same source. Native verification has 1,473
passing cases across seven suites; Projection still started zero of 41 cases in
`42204637743`. The original full qualification remains failed. The
[feature evidence](fork/evidence/feature-verification-c55e19c4.json) records the
fixture coverage and remaining gates; a current service diagnostic is not a
claim of successful Projection execution or a fully qualified release.

Sign the candidate using the same app/team identity as the existing installation;
**do not uninstall the app to update it**. An unsigned package is not installable
until a valid signer succeeds. The original full build82 remains available in
[artifact 13479087810](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42160468600/artifacts/13479087810)
with IPA SHA-256 `addaaf68000d010b84cea0d9798b9a234f43bfabc62845cdbf18fc5f14c4605e`.

Verify the current IPA in Windows PowerShell:

```powershell
Get-FileHash .\AyuGram-c55e19c4-main-app-only-unsigned-device.ipa -Algorithm SHA256
```

## Historical build 54

The older SideStore candidate is the **main-app-only unsigned ARM64 device IPA**
from [run 42075783146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146).
Download [artifact 13438212892](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146/artifacts/13438212892)
and extract `AyuGram-62546557-main-app-only-unsigned-device.ipa`.
Its IPA SHA-256 is
`9ee09c4396af01b8c6d6fc521ecd767b16d9a0ef4e90d630785eb5fbc3931f05`.
Verify it with Windows PowerShell:

```powershell
Get-FileHash .\AyuGram-62546557-main-app-only-unsigned-device.ipa -Algorithm SHA256
```

That historical source includes startup/Keychain/video repairs, and its matching simulator
variant passed two authorization-screen launches with private storage retained.
Six extensions are omitted; app/framework bytes are preserved. This IPA is
unsigned, requires independent signing, and has not been physically verified on
the owner's iPhone. It does not establish automatic private fallback or a logged-in
Telegram session. Read [SideStore installation and update precautions](SIDESTORE_INSTALL.md)
for the precise evidence and feature limits. Keep the same app identity and
Apple account when updating; do not delete the existing app to install an update.

The original full unsigned ARM64 device package is build **54**, source
`62546557807ee5d8ff468ea3d7d46ce1a090c8cf`, from
[run 42031063759](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759).
Download its [device artifact 13421543948](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759/artifacts/13421543948)
and extract `fork-baseline-unsigned-device/Telegram.ipa`. Its IPA SHA-256 is
`da12c38d0a14f6331b381b9b71f25fe191e1ad6f2034109c751e2e5799101d4b`.
The artifact ZIP has a different checksum. The package contains no code signatures
or embedded provisioning profiles. All twelve executable binaries were checked
as ARM64 iPhoneOS, and their embedded framework dependencies resolve. It still
requires signing before installation; static inspection is not a runtime test.

The same application source passed 104 focused native repair tests before device
compilation. Full native testing passed 1,134 cases across seven suites, while
Projection executed zero because the XCTest host did not start. Later startup
replays displayed the actual authorization screen with the simulator extensions
removed. The new startup repair has not been physically verified on the owner's
iPhone. The older `9a040579` installation was reported to remain black at launch;
it is not the current recommended candidate. See [SideStore startup evidence and
update precautions](SIDESTORE_INSTALL.md) for that historical main-app-only control
and the current extension-free package listed above. This is not a qualified
release or a claim of feature parity.

## Current build and signing workflow

Use **Archive application integration (hosted macOS)** on the single
`ci/verify-latest` verification branch. Feature/PR commits no longer start three
redundant macOS workflows. Advance that branch once a source batch is ready;
one global qualification queue preserves its active run and retains only the
latest pending successor. Existing submitted jobs keep their original settings.
See [queue policy and observed capacity failure](fork/CI-QUEUE.md).

The integration and unsigned-device jobs use the same commit and may execute
alongside each other. Full qualification runs native gates and simulator
compilation in one private build job; a native gate failure can prevent app
compilation. The device job is independent. The separate simulator diagnostic
`42174474046` compiled the same `c55e19c4` source without rebuilding device82;
it does not replace Projection's remaining gate or change the original failed
qualification. Manual standalone workflows are diagnostics, not automatic
second builds. A temporary, hash-verified unsigned rules overlay omits profile
embedding for the unsigned build only. No signing credentials are synthesized.

| Result | Downloadable artifact | What it establishes |
| --- | --- | --- |
| Native evidence | `archive-integration-<commit>` in full scope; `archive-integration-tests-<commit>` in native scopes | Test logs and checksums. Logs alone do not establish a successful app build. |
| Simulator application compilation | `archive-integration-<commit>`; standalone diagnostic `fork-baseline-<commit>` | On compilation success, `fork-baseline-simulator/Telegram.ipa` and its manifest/checksum. Simulator only, not installable on an iPhone. |
| Verified startup variant | `startup-main-app-only-unsigned-device-<source>` | Root-level named IPA, manifest and checksums; six extensions removed. Simulator startup passed; device signing and physical verification remain required. |
| Unsigned device compilation | `archive-unsigned-device-<commit>` | On success, `fork-baseline-unsigned-device/Telegram.ipa` and its manifest/checksum. It still requires independent signing before installation. |
| Windows signing and physical installation | Produced locally by your chosen signing tool | A different, signed package. Installation, entitlements and runtime behavior must be verified separately on the iPhone. |

Match every artifact's commit to the intended PR head. Failed application jobs
can still upload diagnostics without a package; a downloadable ZIP alone does
not establish build success. The separate **Archive engine draft tests** workflow
publishes library test evidence only. Earlier **Fork baseline (hosted macOS)**
runs retain their `fork-baseline-<commit>` artifact name and their original job
layout. A successful run of one branch does not validate another branch.

The numbered build-output paths below describe a full device package. For an
explicitly identified extension-free artifact, use its named root-level IPA and
matching checksum, then follow the same local signing steps.

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
   manifest. Before publication, the workflow checks bundle metadata and every
   embedded unsigned ARM64 iPhoneOS image, rejects signatures/profiles and
   resolves bundled load-command dependencies. System-library availability,
   dynamic loading, valid signing and runtime behavior remain separate checks. Select the **device** output; a simulator IPA cannot run on an iPhone,
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

Sideloadly re-signing is **unverified for this fork**. The full package includes
extensions and depends on matching App Groups, keychain groups
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

For personal signing, iOS 27.2 black-screen investigation and data-preserving
updates, see [the SideStore guide](SIDESTORE_INSTALL.md). Full qualification
builds use their workflow build number. The separate current simulator diagnostic
used build1; the paired original device candidate retains build82 unchanged.
