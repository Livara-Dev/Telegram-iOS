# Keychain and encrypted playback repair qualification

The Keychain API and encrypted-player repairs passed **104 native iOS cases**
for application source `62546557807ee5d8ff468ea3d7d46ce1a090c8cf` in
[focused run 42027769348](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42027769348).
The same 16 PasscodeCore, 77 archive-store and 11 playback cases passed again for
`c484babe67fcb1d9a87ed9a034edc3f292516e0d` in
[targeted run 42058341829](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42058341829).
Both artifacts' outer and inner checksums were verified. The later run still
failed because Projection executed zero cases; it is not an all-green run.

The completed [full run 42031063759](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759)
compiled and published an unsigned ARM64 device IPA. Seven native suites passed
1,134 cases, but Projection startup and the original simulator launch smoke
failed. Subsequent artifact-only run
[42075783146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146)
passed two real launches with explicitly pinned private storage and published a
separate unsigned main-app-only IPA. This verifies the tested simulator variant.
No signed IPA, physical iPhone startup repair, automatic private fallback,
Telegram authentication session or release qualification is claimed.

## Requested compilation and playback repair

Run [42008457157](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42008457157)
built source `32ee28da`. Both complete-application jobs stopped compiling
`Passcode.swift:127`: `kSecUseAuthenticationUIFail` is deprecated and the module
correctly treats warnings as errors. That run produced no new IPA or startup
smoke result.

The repair uses the documented public replacement: a fresh `LAContext` with
`interactionNotAllowed = true` passed through `kSecUseAuthenticationContext`.
The same deprecated archive Keychain option and test expectations are updated.
Access groups, service namespaces, non-sync flags, Data Protection and failure
semantics are preserved. Ordinary interactive passcode reads are unchanged;
compiler warnings have not been suppressed.

The native controls distinguish playback from storage. The protected synthetic
MP4 decoded, and byte-for-byte archive reopening passed. The original encrypted
and in-memory custom loaders failed with AVFoundation -11800 / POSIX 1 after
one metadata request and zero byte-range requests. Those results did not identify
a SQLCipher or fixture corruption problem.

Each repaired asset uses an opaque random URL so failed loads and different
accounts cannot share a resource-cache identity. Completed local media advertises
`isEntireLengthAvailableOnDemand` on supported iOS versions. The `.forbidAll`
external-reference restriction and exact-URL delegate guard stay in place.
No private API, HTTP listener, external upload or plaintext-media fallback was
introduced. All 11 hosted playback cases pass, including independent valid assets
after a failed load. This verifies those loader paths, not live message reception
or physical-device playback.

## Native test evidence and the unresolved Projection runner

The full native job for `62546557` completed with these results:

| Suite | Executed cases | Result |
| --- | ---: | --- |
| PasscodeCore | 16 | Passed |
| Postbox | 201 | Passed |
| LocalMessageArchive | 77 | Passed |
| TelegramCore | 543 | Passed |
| LocalArchivePlayback | 11 | Passed |
| TextFormat | 270 | Passed |
| SettingsUI | 16 | Passed |
| ChatLocalTextFilterProjection | 0 | Runner startup failed |

The seven executed suites total **1,134 passing cases**. Projection did not
produce a usable test result bundle or start a case; it must not be counted as
passing. Replacing its test host in `c484babe` did not resolve that failure.
The targeted repeat passed the three repaired suites again, but Projection still
executed zero cases. Its cause remains unresolved; further diagnostics are scoped
to test-host launch/injection rather than another complete application build.

The first focused run `42022644780` compiled the new APIs but found
`interactionNotAllowed` false in a hostless XCTest process; playback still failed
before a byte read. Three small UIKit test hosts now supply separate suite
identities and biometric usage descriptions. Existing assertions and external
reference protections remain. The subsequent iOS passes support an
application-host explanation. The successful
[macOS probe 42027565183](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42027565183)
was a supporting control, not iOS qualification.

## Produced device artifact

Device job `127257336871` succeeded for `62546557` and published
[artifact 13421543948](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759/artifacts/13421543948).
Its inner IPA is build `54`, bundle `org.livara.ayugram`, with SHA-256:

`da12c38d0a14f6331b381b9b71f25fe191e1ad6f2034109c751e2e5799101d4b`

The artifact ZIP has a separate digest in the evidence JSON. Static inspection
checked 12 ARM64 iPhoneOS Mach-O binaries and 41 framework dependency references,
with no embedded code-signature commands or provisioning profiles. This is an
**unsigned device package**, requiring independent signing before installation.
It has not been installed or tested on a physical iPhone by this work.

The newer **main-app-only unsigned variant** is available as
[artifact 13438212892](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146/artifacts/13438212892):
`AyuGram-62546557-main-app-only-unsigned-device.ipa`, with SHA-256:

`9ee09c4396af01b8c6d6fc521ecd767b16d9a0ef4e90d630785eb5fbc3931f05`

It removes six extensions; extension features are unavailable in this variant.
Packaging checked 880 retained members for byte and metadata equality with the
original device IPA. Application code, frameworks, bundle ID and build number
remain unchanged. The downloaded artifact's outer digest and both inner
checksums were independently verified, and its manifest binds the exact passing
startup proof and result hashes. It still requires independent signing and has
no verified physical-device installation or startup result.

The active Bazel `TelegramInfoPlist` omits `AyuGramBaseBundleIdentifier`; the
addition in `InfoBazel.plist` is not used by that generator. With the unchanged
runtime bundle ID, the existing runtime group candidate and private-path logic
remain available. Recovering an original App Group after a signing tool rewrites
the bundle identifier is not supported or verified by this build. Keeping the
same app identity and signing account remains necessary for data-preserving
updates.

The simulator application also compiled and passed 270 TextFormat cases in its
own job. Its [artifact 13426711346](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759/artifacts/13426711346)
was verified against its outer digest and all 10 inner payload checksums. The
simulator IPA SHA-256 is
`8ed00f94e2b72bf4ae239c1ea3060b00445107f677a370d6dd59bb3edc2df4c4`.
Static structure, platform, resource-seal and framework-reference checks passed.
Apple's deep/strict `codesign` verification subsequently passed in the artifact
replays. None of these checks alone establishes installation or a rendered UI.

## Actual startup observations without recompiling the application

- The original full-run smoke retained zero launches and no visual evidence.
  [Replay 42054044158](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42054044158)
  verified Apple's signatures but timed out during installation, before launch.
- [Main-app-only replay 42059353808](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42059353808)
  removed six extensions, preserved the main executable's code, frameworks and
  resources, re-signed only the simulator main app, and installed it. A retained
  screenshot shows Telegram's welcome screen. The interpreted Swift screenshot
  checker timed out; the then-current report failed to retain the launch marker.
  This is visual launch evidence, not a completed two-launch smoke pass.
- [Replay 42063434737](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42063434737)
  compiled the headless CoreGraphics/ImageIO checker and passed all three native
  synthetic image controls. The real app reported `authorizationVisible` with no
  startup failure. The strict private-storage assertion then failed; it was not
  relaxed or counted as a pass.
- [Replay 42068575839](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42068575839)
  confirmed a valid, persisted group selection using 17 boolean-only diagnostics.
  The real app again reached `authorizationVisible`. Its second fresh simulator
  timed out during installation before the private control could seed or launch.

The source simulator app has no App Groups in its code-signature entitlement
blob, but its Mach-O `__TEXT,__entitlements` section contains a matching group.
An absent signature entitlement therefore does not establish automatic private
fallback in Simulator. The observed automatic group selection is legitimate
startup evidence and is retained separately. No Mach-O patch, private API or
application-source change was used to force a different result.

The first private-only control,
[42073351166](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42073351166)
from tooling source `2d378caf`, reported another install-phase timeout before
seeding or launching. Its native image checker compiled and all three synthetic
controls passed. Both simulator cleanup commands also reported timeouts. The
artifact's outer digest and six inner checksums were verified. No private-storage
assertion ran and no additional device IPA was published.

A portable reproducer then established a command-supervision bug: the wrapper
waited for stdout/stderr EOF before checking whether its direct child had exited.
A descendant retaining those descriptors could therefore cause a false timeout
after a successful child exit. The repair polls the child and limits post-exit
pipe draining to 0.2 seconds, preserving nonzero status and the 64 KiB output cap.
Live-child timeout regressions also pass. The 120-second installation cap and
all application/storage/readiness gates are unchanged. This bug's role in the
previous CI timeouts is **unproven**; no CoreSimulator or package cause is claimed.

The corrected private-only replay,
[42075783146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146),
completed successfully from tooling source
`a3533da1e1352b602f5705b75488a98616c389d1`. The repair is integrated in
`12b7f9864dc406564951d524de69b1f1894e27b6`. Its published startup artifact was
independently verified against the outer digest and all 10 inner checksums.
Both launches reported `authorizationVisible` without a startup failure and
produced nonblank screenshots. All three native image controls passed.

The one fresh simulator had no preferences, launch marker or account store before
the public private-selection preference was seeded. The real application then
created private `accounts-metadata`, retained the private preference on both
opens and preserved the same account-store directory identity across restart.
Only the directory-stability boolean is retained. These observations verify
private-container startup and directory persistence; they do not establish
message-content persistence or a Telegram login.

The independently verified device-variant manifest binds the passing startup
proof and result hashes and records that no automatic selection control ran.
This explicitly pinned pass cannot verify automatic private fallback or physical
SideStore behavior. It permits publication of the separately named unsigned,
extension-free device IPA described above. The original full device artifact
remains available. No full application recompilation was needed, no installation
timeout was increased, and the earlier failures remain recorded. The portable
supervision bug is fixed; its role in those CI failures remains unproven.

## CI scopes and evidence boundaries

`focused` tests the three repaired native suites. `projection` repeats those
regressions plus the unresolved Projection target. A dedicated `projection-only`
capture attempt, [42073979469](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42073979469),
failed during Bazel analysis because its diagnostic runner needed a `testonly`
designation; it executed zero test cases. That configuration is fixed in
`f48c9b04`, and [42075582797](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075582797)
is active at this checkpoint. No resolution of the original Projection startup
failure is claimed. `probe` runs small native host controls. `startup` reuses the
hash-verified application artifact and never compiles Telegram code.
Full device/simulator compilation remains attached to the canonical verification
branch. These diagnostic runs have not repeated the full application build.
Active jobs are preserved and startup replay uses a separate source-scoped queue.

The integrated tooling at `12b7f986` passed **221 portable Python tests**. This
count covers build/diagnostic/artifact tools, including subprocess regressions;
it is separate from the native iOS case counts above and does not verify an app.

A successful compiler, library suite or simulator control does not prove live
Telegram authentication, deleted-message/media capture, every AyuGram feature,
physical-device startup or an installable signed IPA. Those remain separate
qualification requirements.
