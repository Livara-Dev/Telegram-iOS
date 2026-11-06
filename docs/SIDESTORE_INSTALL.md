# SideStore builds, startup and updates

The owner reports that the `9a040579` device IPA installed on an iPhone 15 Pro
running iOS 27.2, then remained black at launch. Installation is user-reported;
we have no device crash log or proof of the re-signed entitlements.

The current compiled **unsigned ARM64 device candidate** is build **82**, source
`c55e19c4f5cbe0d730499035d0a0ef712f6903a9`, from
[run 42160468600](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42160468600).
Download [artifact 13479087810](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42160468600/artifacts/13479087810)
and extract `fork-baseline-unsigned-device/Telegram.ipa`. Its IPA SHA-256 is:

`addaaf68000d010b84cea0d9798b9a234f43bfabc62845cdbf18fc5f14c4605e`

All twelve unsigned ARM64 iPhoneOS images, five frameworks and 113 bundled
load-command dependencies were checked. The matching source compiled as a full
simulator app in [42174474046](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42174474046),
build **1**; that separate run repeated all 270 TextFormat cases successfully.
The original qualification passed **1,473 actual native cases** across seven
suites, including all sixteen Passcode and eighteen encrypted-playback cases.
Projection started zero of its 41 cases, so full qualification remains failed.
The repeated TextFormat suite is not additional unique coverage.

[Current startup replay 42191212317](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42191212317)
passed source/signature/resource checks but timed out in `simctl install` after
120 seconds, before any application launch. No current-source startup pass,
main-app-only device package or physical-device repair is established by that
run. This original full candidate includes six extensions and still requires
valid signing before installation. Artifact ZIP and IPA checksums differ.

## Historical build 54 startup control

The older **main-app-only unsigned ARM64 device IPA** is available from
[successful run 42075783146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146):
[download artifact 13438212892](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146/artifacts/13438212892).
Extract `AyuGram-62546557-main-app-only-unsigned-device.ipa`. Its SHA-256 is:

`9ee09c4396af01b8c6d6fc521ecd767b16d9a0ef4e90d630785eb5fbc3931f05`

The artifact ZIP has a different checksum. The IPA contains the existing startup,
Keychain and encrypted-video repairs from application source
`62546557807ee5d8ff468ea3d7d46ce1a090c8cf`, build `54`, bundle
`org.livara.ayugram`. Its six extensions are removed; all 880 retained entries
match the original device package's bytes and metadata. All six remaining binaries
are unsigned ARM64 iPhoneOS; all five frameworks and their dependency paths are
present. It needs SideStore or another valid signer before installation.

The matching main-app-only simulator control passed two real launches with the
native authorization UI visible, nonblank screenshots and the same private
account-store directory after restart. That test explicitly pinned the public
private-storage selection before first launch in an empty disposable simulator.
It does **not** establish automatic fallback, successful Telegram login,
physical iPhone behavior or archive-content recovery. No authentication codes or
session material were used. The physical black-screen repair remains unverified.

At historical source `62546557`, the 104 focused native repair cases passed
before device compilation and again in a later native repeat. Full native integration passed 1,134 cases across
seven suites, but Projection executed zero because its XCTest host did not start.
Its Projection diagnostics did not establish a startup cause. This historical
unsigned candidate is not a fully qualified release. The [original full device package](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759/artifacts/13421543948)
remains available, with IPA SHA-256
`da12c38d0a14f6331b381b9b71f25fe191e1ad6f2034109c751e2e5799101d4b`.

A source audit found an early return when `group.<runtime bundle identifier>`
is unavailable. It occurs before account/authentication initialization and tries
to present an alert before the window attaches to a scene. A personal signing
profile or rewritten identifier can trigger this path. This is a reproducible
source defect, not a confirmed diagnosis of the user's signed binary.

## Startup changes and remaining verification

The main application selects its accessible configured App Group or its private
Application Support container. It persists that selection. If a previously
selected shared container becomes unavailable, it fails visibly instead of
opening a different, empty account store. The storage-selection code performs
no database migration, removal or reset. Private mode uses separately namespaced
Keychain services and does not reset legacy shared-installation secrets. Existing
upstream account-database recovery remains unchanged, including conditional
recreation after an unreadable encryption-key probe. See the [storage review and
remaining risks](SECURITY_ASSESSMENT.md#startup-and-storage-review-2026-11-05).
Authentication, MTProto and encryption algorithms remain upstream.

Build 54's active Bazel-generated metadata does not contain
`AyuGramBaseBundleIdentifier`; the earlier `InfoBazel.plist` addition is not used
by that generator. Stable `org.livara.ayugram` identity and private fallback are
unaffected. A signing-tool rewrite of the bundle ID combined with access to the
original App Group is not a supported or verified recovery path in this build.
Current `c55e19c4` builds include the active metadata key with
`org.livara.ayugram`. This does not authorize a bundle/team identity change or
prove recovery of a previous App Group. Keep the same identity when updating;
do not use a rewrite to recover old data.

Missing storage or Keychain access now has a scene-visible local status screen.
If startup takes longer than 20 seconds, a bounded status view offers Copy startup
status and Continue waiting. The status contains only fixed stage/error names;
it excludes paths, account IDs, codes, session material and messages. Nothing is
uploaded. Existing passcode UI takes priority over this diagnostic view.

The simulator smoke test installs the actual built simulator IPA onto a new,
disposable simulator. It launches twice without entering credentials, requires a
real authorization/conversation controller and an attached visible window, and
records screenshots/video. A nonblank-screen check is additional evidence; it is
not authentication, SideStore or physical-device verification.

Targeted artifact replays verified the existing package, Apple code-signing
checks and its resources without recompiling Telegram. The original full bundle
timed out during installation. A separate main-app-only simulator control
installed and displayed Telegram's actual Welcome/Start Messaging screen.
Later diagnostics observed `authorizationVisible` without a failure. The simulator
honored an embedded Mach-O App Group even though that group was absent from the
outer code signature; that signature alone does not prove private fallback.

[Run 42075783146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146)
passed the strict private-only control after the command-supervision helper was
corrected. Earlier installation timeouts remain recorded; the helper defect's
role in those particular failures is unproven. The successful replay used the
same compiled app without another Telegram build. Its public private-storage
selection was seeded only into a fresh disposable test container. Both actual
launches reached `authorizationVisible` without failure, retained private storage
and displayed Telegram's Welcome/Start Messaging screen. The app created the
account-store directory and retained its identity across restart. Screenshots,
video and closed diagnostics are in [startup evidence artifact 13438242692](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146/artifacts/13438242692).
The original full package is retained separately.

## Installing a new device artifact

1. Select the intended **device** artifact and its exact source/build above. A
   simulator package will not work on an iPhone even when its CPU is ARM64.
2. Extract its IPA (`fork-baseline-unsigned-device/Telegram.ipa` for current build82). Verify its SHA-256 against
   `manifest.json` and `SHA256SUMS`. The artifact ZIP has a different hash.
3. Import the IPA into SideStore using the same Apple account and app identity
   used for the existing installation. Do not delete the app to update it.
   Replacing an app under the same identity normally preserves its data container;
   changing the bundle identifier/team, removing the app, or losing Keychain/App
   Group capabilities can make existing data unavailable. Keep a device backup.
4. SideStore must sign the main app and all five frameworks correctly. The
   historical main-app-only candidate contains no extensions. The current full
   package also requires correct signing for every retained extension.
   Private-container mode is main-app-only: sharing, widgets, notification
   extensions and other shared-container functions cannot access that data.
   APNs and other restricted capabilities still require valid Apple provisioning.
   This client cannot manufacture those entitlements.
5. If startup shows a diagnostic, copy only that status. Preserve the installation
   and its data. Do not send passwords, Telegram codes, API hashes or session files.

Automatic in-place replacement, differential executable loading, bypassing iOS
code signing, and remote Swift code execution are not implemented. Every native
code update needs a new IPA and valid signing. Public dependency caching does
not cache account data, signing material, generated API constants or compiled
objects. Sub-ten-minute build times have not been measured or promised.

No new build should be described as fixing the physical device until that build
has actually been installed and the authorization/chat interface is observed.
