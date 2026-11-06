# SideStore builds, startup and updates

The owner reported a black screen after an older IPA installed on iPhone 15 Pro
running iOS 27.2. Later screenshots show separate SideStore signing errors; no
physical-device success is established for the current candidate below.

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

The package above remains the retained c55 candidate; it does not contain the
newer secret/timed-retention changes from application source `5d241267`. Both 5d
applications compiled in [run 42279866706](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42279866706).
Later artifact-only [recovery 42306472313](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42306472313)
again timed out during installation, before any app launch or captured media.
Its SDK status, initialization and recheck commands returned zero; shutdown timed
out, but exact owned-device deletion and absence were verified. Startup remains
unverified; compilation and static package checks are not runtime verification.
The derived [d020 Core candidate](fork/NATIVE-CORE-VERIFICATION-D020BFD7.md)
contains an Account startup-ordering relocation absent from the frozen 5d binary;
its component tests do not qualify 5d app startup. See the
[source-separated checkpoint](fork/CURRENT-VERIFICATION-2026-11-06.md).

The matching retained c55 main-app-only simulator application reached the actual
AyuGram authorization screen twice on a fresh **iPhone 15 Pro / iOS 27.0** device.
Both screenshots and the complete 390-frame variable-rate recording were
visually reviewed. The app created its private account-store directory and kept
the same identity after restart. Private storage was explicitly selected before
launch; this does **not** prove automatic fallback, Telegram login, authenticated
Settings navigation, SideStore signing, iOS 27.2 behavior or a physical-device
black-screen repair. No Telegram login codes or existing account session material
were used during replay. The app retains its registered API configuration.

The device and simulator retain separate origins: device82 from `42160468600`,
simulator1 from `42174474046`, at the same source. Earlier c55 native verification
passed 1,473 cases across seven suites. Projection started zero of 41 cases in
historical `42204637743`; that attempt and the original full qualification remain
failed. Later [run 42281520123](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42281520123)
passed all 41 retained c55 Projection payload cases plus one discovery wrapper;
a separate control passed one case. The retained application and Projection
payload were not recompiled. This different experimental discovery harness did
not execute the original Bazel host or qualify a release, physical device or the
newer 5d application. The [current verification checkpoint](fork/CURRENT-VERIFICATION-2026-11-06.md)
separates these results; the [historical feature evidence](fork/evidence/feature-verification-c55e19c4.json)
preserves the earlier fixtures and failure records. Counts from separate runs
and sources are not a combined unique release total.

Sign the candidate using the same app/team identity as the existing installation;
**do not uninstall the app to update it**. An unsigned package is not installable
until a valid signer succeeds. The original full build82 remains available in
[artifact 13479087810](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42160468600/artifacts/13479087810)
with IPA SHA-256 `addaaf68000d010b84cea0d9798b9a234f43bfabc62845cdbf18fc5f14c4605e`.

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

## SideStore HTML 403 and request timeout

The supplied Error Log shows `SideSign.ServerError 1` with an HTML “403 Forbidden”
response and `NSURLErrorDomain -1001` for a request timeout. These are signing /
network-stage errors, not evidence that Telegram launched and crashed. The
[SideSign error source](https://github.com/SideStore/SideSign/blob/752d1ef602aa792a432ffa1e71646dc1ecf7363c/Sources/Models/Errors.swift)
and authentication/developer-service dispatchers use this generic error for
several endpoints; the screenshot alone does not identify authentication versus
provisioning, an account agreement, a quota, or the exact failing service.

Use official SideStore release guidance and its
[installation prerequisites](https://github.com/SideStore/SideStore-Docs/blob/main/docs/installation/prerequisites.mdx).
They require Wi-Fi and LocalDevVPN for installation/refresh; the screenshot does
not establish whether those prerequisites were active. Check applicable VPN/DNS
interference and Apple developer-account agreements in Apple's own website.
The official 0.7.0-alpha release describes an Apple-server-change/503 repair;
that is not evidence that it fixes this particular 403 or that the user's version
is affected. Never send Apple credentials, Telegram codes, profiles or pairing
files to this repository, developer services or chat.

The extension-free candidate reduces provisioning work to the main application;
it cannot repair an Apple authentication response. Apple's Personal Team limits
still apply. Keep the existing app installed and its identity stable while
investigating; deleting the app/certificates or changing teams is not a required
step in this procedure. Physical signing, installation and runtime remain to be
verified separately.

## Installing a new device artifact

1. Select the intended **device** artifact and its exact source/build above. A
   simulator package will not work on an iPhone even when its CPU is ARM64.
2. Extract `AyuGram-c55e19c4-main-app-only-unsigned-device.ipa`. Verify its SHA-256 against
   `manifest.json` and `SHA256SUMS`. The artifact ZIP has a different hash.
3. Import the IPA into SideStore using the same Apple account and app identity
   used for the existing installation. Do not delete the app to update it.
   Replacing an app under the same identity normally preserves its data container;
   changing the bundle identifier/team, removing the app, or losing Keychain/App
   Group capabilities can make existing data unavailable. Keep a device backup.
4. SideStore must sign the main app and all five frameworks correctly. This
   current main-app-only candidate has no extensions. The optional original full
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
