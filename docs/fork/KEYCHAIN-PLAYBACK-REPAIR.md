# Keychain and encrypted playback repair qualification

Current tested source: `62546557807ee5d8ff468ea3d7d46ce1a090c8cf`.
[Focused iOS run 42027769348](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42027769348)
passed **104 cases**: 16 PasscodeCore, 77 archive-store and 11 playback cases.
The test artifact's outer ZIP and inner checksums were verified. The earlier
[macOS probe 42027565183](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42027565183)
passed and supports the diagnosis; it is not iOS proof.

After that focused pass, a push of the same source automatically started
[full run 42031063759](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759).
No full run was manually dispatched. Its unsigned device job succeeded. The
simulator application compiled and passed 270 TextFormat cases, but its startup
smoke failed before any observed app launch. Full native integration is still
running. The build is not release-qualified, and physical-device startup is
unverified.

Run [42008457157](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42008457157)
built source `32ee28da`. Both complete-application jobs stopped compiling
`Passcode.swift:127`: `kSecUseAuthenticationUIFail` is deprecated and the module
correctly treats warnings as errors. No startup smoke or new IPA was produced.

The repair uses the documented public replacement: a fresh `LAContext` with
`interactionNotAllowed = true` passed through `kSecUseAuthenticationContext`.
The same deprecated archive Keychain option and test expectations are updated.
Access groups, service namespaces, non-sync flags, Data Protection and failure
semantics are preserved. Ordinary interactive passcode reads are unchanged;
compiler warnings have not been suppressed.

The native controls distinguish playback from storage. The protected synthetic
MP4 decoded, and byte-for-byte archive reopening passed. Both the encrypted and
in-memory custom loader failed with AVFoundation -11800 / POSIX 1 after exactly
one metadata request and zero byte-range requests. These results do not identify
a SQLCipher or fixture corruption problem.

The playback repair gives each asset an opaque random URL so failed loads and
different accounts cannot share a resource cache identity. Completed local media
also advertises `isEntireLengthAvailableOnDemand` on supported iOS versions.
The `.forbidAll` external-reference restriction and exact-URL delegate guard stay
in place. No private API, HTTP listener, external upload or plaintext-media
fallback is introduced. The hosted iOS playback suite now passes all 11 cases,
including independent valid assets after a failed load. This verifies the tested
loader paths, not the complete messaging application or a physical device.

## Focused validation before any full build

The existing qualification workflow accepts `validation_scope: focused` through
manual dispatch. On `integration/ayu-current` it runs only PasscodeCore, archive
storage and archive playback tests. Device and simulator application builds are
skipped even if `unsigned_device` is true. The evidence explicitly identifies the
three requested targets and does not claim full integration or an IPA.

Only after this same source passes the focused native tests should it advance to
`ci/verify-latest`, whose push event retains normal full qualification. Active
jobs are preserved. This avoids repeating full builds to diagnose a small repair.
A successful device compilation still requires the independent real-app startup
smoke and physical SideStore testing before claiming the black-screen issue is
resolved on iPhone.

## First focused result and test-host controls

Focused run `42022644780` compiled the replacement Keychain APIs, but the native
assertions found `interactionNotAllowed` false immediately after setting it on a
fresh context. Playback still failed before a byte read; opaque URLs and the
local-availability hint alone did not resolve the decoder error. No full build
was triggered after this result.

The original tests execute as XCTest logic tests without an application host.
Both LocalAuthentication and AVFoundation use system services and process
identity. Three minimal UIKit test hosts now provide separate suite identities
and an explicit biometric usage description. Assertions and external-reference
protection are retained. The subsequent hosted iOS focused pass supports the
application-host diagnosis; the macOS controls alone would not establish it.

The manual `probe` scope runs only small native macOS controls without Bazel,
Telegram credentials or application builds. It compares command-line and signed
app-bundle contexts, plus public AVFoundation policy/response controls using the
hash-pinned synthetic fixture. These controls are diagnostic code outside the
application; a macOS result cannot establish iOS functionality. The fixed iOS
focused suites must still pass before full qualification.

## Device artifact and remaining startup evidence

The same-source device job `127257336871` succeeded and published
[artifact 13421543948](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759/artifacts/13421543948).
The IPA is build `54`, bundle `org.livara.ayugram`. Its SHA-256 is
`da12c38d0a14f6331b381b9b71f25fe191e1ad6f2034109c751e2e5799101d4b`;
the artifact ZIP has a separate hash recorded in the evidence JSON. Package
inspection checked 12 ARM64 iPhoneOS Mach-O binaries and 41 framework dependency
references, with no embedded code-signature commands or provisioning profiles.
It requires independent signing before installation.

The active Bazel `TelegramInfoPlist` does not embed `AyuGramBaseBundleIdentifier`;
the earlier `InfoBazel.plist` addition is not used by that generator. The stable
bundle ID and private-container fallback are unaffected. Recovering the original
App Group after a signing tool rewrites the bundle identifier is not supported or
verified by this artifact. Preserve the same app/signing identity for updates.

The full native job remains active. Simulator job `127257336838` completed with
a failed startup step after successful application compilation and 270 TextFormat
cases. Its smoke report contains zero launches and no screenshots, video or
recording log. The failure occurred before app observation; creation, boot or
installation remains the unconfirmed cause. It is not evidence of a running-app
black screen or a confirmed physical-device repair.

[Simulator artifact 13426711346](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759/artifacts/13426711346)
was verified against its outer digest and all 10 inner payload checksums. Its
simulator IPA SHA-256 is
`8ed00f94e2b72bf4ae239c1ea3060b00445107f677a370d6dd59bb3edc2df4c4`.
Safe Linux extraction and static checks passed: all 12 Mach-O binaries target the
simulator, code-signature structures/page and resource hashes are consistent, and
framework reference checks resolve. These checks do not substitute for Apple's
`codesign`, `installd`, a rendered interface or physical iPhone testing.

## Targeted startup replay, without another full compilation

A tool-only `startup` scope is prepared to reuse the verified simulator IPA from
source `62546557807ee5d8ff468ea3d7d46ce1a090c8cf`. It validates the run, successful
compile step, artifact provenance, outer and inner hashes, manifest and platform.
The tool checkout may differ only in build tools, this workflow and documentation;
application, Bazel and submodule changes are rejected. This diagnostic replay has
not run at this checkpoint and cannot qualify the failed source run by itself.

The replay checks the original package with Apple `codesign` before launching,
then runs a fresh, data-free normal control with closed command-phase/error
reporting. The original simulator package already lacks App Group entitlements,
so the normal control must show private storage on two launches. A redundant
re-signing variant is skipped in that case. A separate source-scoped queue
preserves the still-running native qualification job. No new full app build or
physical-device verification is claimed.
