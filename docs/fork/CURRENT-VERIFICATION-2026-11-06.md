# Current verification checkpoint — 6 November 2026

The new archive source `5d241267735b3958c225681d173853568bab705d` passed **964 actual native XCTest cases** in [run 42272821047](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42272821047): 129 archive-store and 835 TelegramCore cases, with no failures, skips, duplicate case events or invalid start/pass ordering. All 28 added method identities passed; one replaced an old name, so the net increase is 27 methods. This is a focused local-integration qualification. It did not compile the complete application or produce an IPA.

The retained install candidate still contains application source `c55e19c4f5cbe0d730499035d0a0ef712f6903a9`. It does **not** contain the newer secret/timed-retention or media-handoff changes. Its two simulator authorization-screen launches do not verify authentication, automatic storage fallback or installation on a physical iPhone. No signed device installation or iPhone performance result is claimed.

Both complete source-5d applications subsequently compiled in [42279866706](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42279866706), with all 270 TextFormat cases passing. Installation timed out before any app launch, then shutdown/deletion also timed out. The private-device-set, artifact-only recovery [42300627717](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42300627717) reproduced the installation/cleanup boundary without rebuilding either app. Its artifact contains zero launches, PNGs or video; packaging remained skipped. Neither failed run is an app-crash, black-screen, authentication or device-installation verification. No underlying cause is established.

The SDK-initialized artifact-only [recovery 42306472313](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42306472313) also failed during installation, with zero launches, PNGs or video and no final startup-qualified package. The SDK status, initialization and recheck commands each returned zero. Its bounded installer observer accepted no process, sample, host-pressure or container measurements; it does not identify a bootstrap, service or package cause. Shutdown timed out, but exact owned-device deletion and final UUID absence were verified. This remains a failed startup attempt, not an app-crash, authentication, physical-device or release qualification. No device package was produced by this failed recovery.

## Source and artifact ledger

| Evidence | Source and result | Boundary |
| --- | --- | --- |
| Focused archive/Core native suite | `5d241267`; [42272821047](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42272821047), 964 passes | Real Postbox/MediaBox/SQLCipher fixtures and typed-policy tests; no complete app build or live Telegram session |
| Recipient and startup-order Core components | `d020bfd7`; [42295994007](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42295994007), 854 unique passes: 835 existing +16 recipient +3 startup, zero failures/skips | Derived source includes the Account relocation; frozen 5d binary does not. No full Account initializer, application, UI or device execution in this Core-only run. [Exact coverage](NATIVE-CORE-VERIFICATION-D020BFD7.md) |
| Archive/Core evidence artifact | [13533034081](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42272821047/artifacts/13533034081); SHA-256 `7b67ad73d3338a48fb1e04b009db6c2650b9109302a573edd49c0ae458f2790c` | Outer ZIP and all 12 inner checksum references verified |
| Full visible “Custome options” interaction | `c55e19c4` production renderer; [42250781611](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42250781611), one native XCUI case passed | Account-free host with synthetic callbacks; authenticated Settings bindings unverified |
| Settings visual evidence | [13520473259](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42250781611/artifacts/13520473259); SHA-256 `4275c45cfb5fef81596014474a9207b21a9ff4a69c7359a4fc1d6017489805e8` | All 12 original PNGs, 2,599 original video frames and 226 flagged frames reviewed by root and an independent reviewer; all 20 checksum references verified |
| Standard-discovery retained Projection | [42281520123](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42281520123), tooling `6a3ecea5`, retained application source `c55e19c4`; 41 payload + 1 wrapper passes, separate control 1 pass | Exact original case identities and Apple 42-case metadata verified; zero exits, installed bytes, signatures and both cleanup gates passed. Different experimental harness; no original Bazel host, release or physical-device qualification |
| Retained Projection evidence | [13535374914](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42281520123/artifacts/13535374914); SHA-256 `3f103236b5ac2d23d2869981374f19a6faff3d55b7b1a452e417ef59496cd4ec` | Outer ZIP and all 7 inner references verified; all 31 original files / 165,736,765 bytes unchanged. No application or captured-test rebuild |
| New 5d unsigned device compilation | [42279866706](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42279866706), device job `128105899974`, build 1001; [internal artifact 13539444219](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42279866706/artifacts/13539444219) | Device and matching simulator compilation passed. Startup remains unverified after installation failures; this internal artifact establishes compile/static proof, not signed installation or runtime behavior |
| SDK-initialized artifact-only startup recovery | Frozen `5d241267`; [42306472313](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42306472313), SDK command exits 0/0/0, install timeout, zero launches/media; [artifact 13546133462](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42306472313/artifacts/13546133462), SHA-256 `ca16bac9c27fdfdc639517144f5a0157dc659c11ef6eca8c6daefadbb688659a` | All 9 inner references verified. Observer unavailable; shutdown timeout, but exact deletion and UUID absence verified. No startup or release qualification |
| Preserved unsigned device candidate | [13495848146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42204637743/artifacts/13495848146), `c55e19c4`, ARM64/iPhoneOS | Main app only; six extensions removed. Requires signing. It is not a signed, installable or physical-device-verified IPA |

Preserved filename: `AyuGram-c55e19c4-main-app-only-unsigned-device.ipa`.

SHA-256: `2cb541e76eb632b5b66c24a5e1937b6042cc230551c418948d75291f69e45cc8`.

The package keeps all 880 retained ZIP entries and their metadata unchanged. Its main executable and five embedded frameworks are unsigned ARM64/iPhoneOS images; all 19 bundled dependency edges resolve. Static package inspection is not runtime or Apple-signing verification. Device build 82 and simulator build 1 keep their distinct original provenance.

## Native coverage at source 5d241267

Counts below partition the 964 passing cases; they must not be added to the earlier c55 totals as if they were new unique coverage. Test names and typed metadata alone do not establish a received, playable Telegram attachment.

| Module | Actual native classes/counts | What passed and what remains open |
| --- | --- | --- |
| Encrypted archive store | `ArchiveStoreTests` 93 | Encrypted persistence/reopen, metadata/edit timelines, retention/search/gallery indexes, ordered receipts and descriptor-backed copy behavior. Process death before encrypted commit remains a media-loss window |
| Local encrypted import/export | `ArchiveTransferTests` 25; `LocalArchiveTransferTests` 12 | Versioned encrypted transfer and account/generation validation. Actual document-picker UI and cross-install Keychain/account migration unverified; no AyuSync transport implemented |
| Keychain archive keys | `KeychainArchiveKeyStoreTests` 11 | Key selection/failure contracts; no physical lock state, SideStore team migration or real provisioning proof |
| Automatic capture and coordinator | Capture 28, coordinator 43, replay 26, acknowledged edit 5 | Real local deletion/edit/outbox/SQLCipher paths, source ownership, replay, account/generation and policy changes. No live two-client sender-deletion or edit recording |
| History removal and remote reconciliation | History removal 12, remote 7, server 15, feed 4 | Local production helper paths before canonical mutation, including cache-cleanup behavior. Actual reconnect/server interaction unverified |
| Archived rows, timeline and navigation data | Deleted history 17 + integration 2; inline history 14 + integration 3; presentation 8; peer-ID safety 5 | Local queries, committed-state updates and safe identifiers. Authenticated full-chat placement, real search/profile/gallery and controls still need app verification |
| Optional secret/timed handling | `LocalArchiveTimedMediaExclusionTests` 47 | Explicit independent opt-ins, remote-consume/helper capture, secret pre-unlink ownership and unconditional canonical cleanup. Full recipient local-consume → timer worker → playback/close path is not yet tested end to end |
| Ghost message reads/presence/interactions | Message privacy 15, presence 9, read-after-send 24 | Account-local policy and typed operation behavior. Sending, reconnecting and simultaneous other-client effects on a real server remain unverified |
| Ghost activity/story protection | Activity 15, stories 26, story-opening warning 10 | Selected outgoing activity categories and story policy/warning fixtures. No perfect-invisibility or server-side suppression guarantee |
| Advanced local filters | Codable 3, media 9, bounded expressions 9, sender/inverse 15, text/rules 18 | Persistence and local classification/matching. The separate 25 retained c55 filter Projection cases passed in experimental discovery run 42281520123; authenticated hide/reveal and rule-editor interaction remain unverified. No spam classifier or notification filtering claimed |
| Local appearance | Cosmetics 8, folder appearance 21 | Account-local preferences, tags/colors and server-entitlement invariance. Native icon switching and authenticated application appearance unverified |
| Local ads/contact/telemetry policy | Ads 5, requirement-to-contact 7, telemetry 3 | Source/local consumer contracts. Not a runtime zero-telemetry audit, no proof that all promotional surfaces are hidden |
| Other upstream Core regressions | 390 | Existing Core/network/message fixtures passed. These do not convert local feature fixtures into authenticated end-to-end tests |

The archive Core rows above total 248 cases, including its 12 transfer cases. The two transfer suites are listed together for clarity, not counted twice in the total. The machine-readable class counts and exact 28 added identities are in [the bounded native evidence record](evidence/2026-11-06-native-archive.json).

The retained c55 Projection verification separately passed 25 filter, 12 archived-row projection and 4 row-renderer cases through framework-managed discovery, plus its one wrapper and separate hosted control. Full-input case parsing, Apple native metadata and observer/run counters agree; bounded public text alone retains only 26 unique payload/wrapper pairs. Mac preflight passed 228 portable cases, including all 67 deferred-plan cases and the exact Foundation gate. The corresponding Linux module passed 66 with one platform skip. Earlier nested attempts failed and remain failed; the successful different harness does not verify the original Bazel host or the separate current 5d application.

Earlier `c55e19c4` had 1,473 passing native cases across Passcode 16, Postbox 201, archive 114, Core 796, encrypted playback 18, TextFormat 270 and SettingsUI 58. Those are earlier-source results. The 18 playback tests decoded generated PNG/JPEG, GIF, PDF, AAC, Ogg/Opus and H.264/audio after encrypted reopen; they do not establish the live capture of the synthetic four-byte timed video/voice fixtures.

The existing archive benchmark in run 42272821047 measured ten warm single-record
appends after 10,000 synthetic snapshots: **1.2307584 ms mean** for the asynchronous
API roundtrip, including XCTest expectation/wait overhead. The runner used an
**iPhone 17 / iOS 27.0 simulator** on hosted macOS 27.0.1, not an iPhone 15 Pro.
There was no latency threshold or device-performance pass criterion. The
[exact samples and log hash](evidence/archive-append-5d241267.json) are retained;
this does not measure scrolling, memory, battery, startup or full-build duration.

## What the visible Settings verification establishes

The fresh full XCUI case exercised the real renderer in light/dark appearances, search/category navigation, a single archive-switch tap with the callback changing off → on, reset cancellation then confirmation, Ocean palette, reduced motion, bundled-icon permission and cosmetic reset. Twelve frame observations and eleven actual UIKit appearance observations passed. A separate fresh root case in [42239698766](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42239698766) verified its root/title contract. The 401.079 seconds is the complete `xcodebuild` command duration, not the test-case duration.

The host uses synthetic account-free callbacks. It does not establish that selected-account settings, navigation, reset or switching work in a logged-in full application. `CustomeOptionsController` and `CustomeOptionsAccountController` are unchanged between c55 and 5d, but the catalog's timed-media explanation changed. The new explanation and extended-retention controls have not been visually verified by the c55 recording. Fixture-toolbar overlap remains. Dark banner text measured 3.543:1 in purple and 4.164:1 in Ocean against its background; neither reaches the 4.5:1 normal-text threshold. Runtime font size/classification was not captured, so this is a conditional contrast limitation, not a whole-interface compliance finding.

The derived d020 recipient cases additionally verify locally completed cloud timed/view-once photo, H.264 video, Opus voice and round-video bytes through real local consume and timestamp-worker paths, encrypted reopen and cache cleanup. Secret photo/voice fixtures verify canonical forced unlink while the writer is held. Three opt-out cases preserve cleanup without claiming archive capture; two incomplete/missing cases verify honest unavailable media. Three startup-order cases include a positive queue-order fixture, an old-order missing-media control and a protected-store failure control. These are controlled local fixtures, not recipient playback UI, live sender events, authenticated account switching or OS-kill tests. See [all 19 exact identities and boundaries](NATIVE-CORE-VERIFICATION-D020BFD7.md).

## Media-preservation limits

Archive capture must already be enabled for that account. Secret-chat and expiring-message options are separate and **off by default**; timed secret content needs both. Copy-protected, excluded, already expired or never received content remains ineligible. Normal timers, reads, encryption and canonical forced cleanup remain active.

Only eligible completed MediaBox files are read. Partial downloads, missing/evicted files and inaccessible protected data cannot be recovered; no extra network fetch or alternative decryption is used. An opened descriptor survives unlink/replacement, but process termination before encrypted commit can still lose media. Cloud files can disappear before the later utility-queue acquisition. Committed encrypted copies are independent of normal media-cache cleanup, while logout/account-directory removal is a different operation and can remove the archive.

A source/queue audit also found a viable cold-start ordering gap: an already-due restored secret timer can run before the archive owner or initial policy is ready, so canonical cleanup can remove the cached attachment before pinning. This is not a measured device incident. Existing missing-owner tests verify the consequence but do not execute full Account initialization. See [the security assessment](../SECURITY_ASSESSMENT.md#restored-timer-startup-ordering--source-5d241267). The frozen 5d build retains this limitation.

Source handoff allows at most 64 descriptors / 256 MiB globally and 16 descriptors per account. Pre-unlink admission leaves headroom: 63 globally / 15 per account. Leases expire after 60 seconds; the account default archive payload budget is 32 MiB, with snapshot overhead included. One file above the effective 256 MiB bridge limit cannot be admitted even with a larger quota. The Postbox outbox is bounded to 256 captures / 8 MiB. Rejection, expiry or pressure can yield metadata with unavailable media. These are limits, not measured throughput or a permanent/lossless-retention claim.

No full recipient UI → local consume → actual timestamp worker → retained playable attachment test has yet passed. Existing opted timed/view-once tests call the remote helper; the local-expiry test calls the expiry helper directly. Source tracing finds the intended incoming local-consume/worker hook, but that does not close the runtime gap. Telegram API-contract conflicts for optional retention/privacy/ads behavior remain disclosed; no approval or exemption is established.

## Remaining release gates

- Preserve the audited experimental-discovery 41-payload/42-native-case pass separately from the still-unexecuted original Bazel host. It does not replace current-source application, authenticated account or release qualification.
- Build and smoke-test the new source once, retaining its own artifact origins, before publishing it as a replacement candidate. Earlier c55 startup does not qualify 5d.
- Verify authenticated archive/search/gallery, account-bound “Custome options”, Ghost and filter behavior in the actual application; add recipient timer-worker and decodable-media coverage.
- Independently sign/install and test the candidate on the physical iPhone. The reported SideSign Apple HTTP 403 and timeout occurred during signing/network requests; they are not evidence of an app runtime crash and are not resolved by static IPA checks.

No physical-device evidence, production Telegram authentication result, live two-client feature test, runtime network capture, memory/battery/scroll benchmark or complete GPL compliance certification is claimed by this checkpoint.
