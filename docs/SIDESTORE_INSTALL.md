# SideStore builds, startup and updates

The owner reports that the `9a040579` device IPA installed on an iPhone 15 Pro
running iOS 27.2, then remained black at launch. Installation is user-reported;
we have no device crash log or proof of the re-signed entitlements.

A source audit found an early return when `group.<runtime bundle identifier>`
is unavailable. It occurs before account/authentication initialization and tries
to present an alert before the window attaches to a scene. A personal signing
profile or rewritten identifier can trigger this path. This is a reproducible
source defect, not a confirmed diagnosis of the user's signed binary.

## Startup changes under validation

The main application selects its accessible configured App Group or its private
Application Support container. It persists that selection. If a previously
selected shared container becomes unavailable, it fails visibly instead of
opening a different, empty account store. No account database is migrated,
removed or reset automatically. The private mode uses separately namespaced
Keychain services and does not reset legacy shared-installation secrets.
Authentication, MTProto and encryption algorithms remain upstream.

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

## Installing a new device artifact

1. Download the unsigned **device** artifact for the chosen source commit. A
   simulator package will not work on an iPhone even when its CPU is ARM64.
2. Extract `fork-baseline-unsigned-device/Telegram.ipa`. Verify its SHA-256 against
   `manifest.json` and `SHA256SUMS`. The artifact ZIP has a different hash.
3. Import the IPA into SideStore using the same Apple account and app identity
   used for the existing installation. Do not delete the app to update it.
   Replacing an app under the same identity normally preserves its data container;
   changing the bundle identifier/team, removing the app, or losing Keychain/App
   Group capabilities can make existing data unavailable. Keep a device backup.
4. SideStore must sign the main app and every retained extension correctly.
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
