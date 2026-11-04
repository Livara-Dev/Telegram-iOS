# Windows build download and iPhone installation

No full-application build or device IPA has succeeded yet. Downloadable test
evidence exists, including passing iOS simulator integration suites, but those
ZIP files do not contain an installable application. These instructions describe
how to obtain and test a device build after the application workflow succeeds.

For PR #2's application integration, use **Archive application integration
(hosted macOS)** on `feature/archive-engine-draft`. Pushes run this workflow
automatically. Its artifact is `archive-integration-<commit>`, and it runs the
Postbox/archive/TelegramCore/TextFormat iOS suites before compiling the full app.
Its separate `archive-integration-tests-<commit>` artifact appears after the
test phase, even while application compilation is still running. This early
artifact contains test evidence only. Private build state is cleaned between
phases, so some dependencies may compile again.
Match the run's commit to the latest PR head. The separate **Archive engine
draft tests** workflow publishes library test evidence only, never an IPA.
The separate `feature/ghost-activity-draft` branch runs the same workflows,
including its added activity-worker tests. A passing archive-branch run is not
evidence for the Ghost branch; use the exact commit of the artifact being tested.

1. Open the authorized fork `Livara-Dev/Telegram-iOS`. In Settings →
   Secrets and variables → Actions, set repository secrets `TELEGRAM_API_ID` and
   `TELEGRAM_API_HASH` from your own application registered at
   https://my.telegram.org/apps. The workflow uses `org.livara.ayugram`, as requested;
   its availability has not been verified in Apple's developer portal. Use your
   own `APPLE_TEAM_ID` for signed builds. Never copy Telegram's identity.
2. Open Actions → **Fork baseline (hosted macOS)** and select the latest automatic
   push run for the baseline branch, or the integration workflow described above
   for PR #2. Manual **Run workflow** is available after the workflow exists on
   the repository's default branch. The workflow
   uses GitHub's `xcode-27` hosted Apple Silicon image, verifies Xcode 27.1/macOS 27,
   verifies the pinned Bazel download, attempts the missing iOS 26.5 test-runtime
   installation, builds the full simulator app and runs the explicit iOS test
   target. The optional next step builds an **unsigned device** IPA.
3. Download `fork-baseline-<commit>` from the successful workflow run. Inspect
   its logs and manifest. Failed jobs may upload diagnostics only; the presence
   of a downloadable ZIP is not proof of a successful application build.
4. Compare the IPA to its SHA-256 checksum in Windows PowerShell:
   `Get-FileHash .\Telegram.ipa -Algorithm SHA256`. Select the **device** output;
   a simulator IPA cannot run on an iPhone, even if both say arm64.
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
The unchanged baseline still contains upstream telemetry. PR #2 adds experimental
archive integration, but application verification is pending. The Ghost draft
adds unverified activity controls only; full Ghost Mode and filtering remain
unfinished. Do not use sensitive accounts
for validation until the documented privacy/security gates pass.
