# Native Core verification — derived candidate

[Run 42295994007](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42295994007) passed **854 distinct XCTest cases: 835 existing + 16 recipient-expiry + 3 startup-order cases**. Every identity started once and passed once, with zero failures, skips, unexpected results, unknown identities or substitutions. Core and All-tests summaries both report 854; the single Bazel XML aggregate is a separate cross-check. Portable checks passed **810 tests (734 baseline + 32 audit + 44 Make)**.

Tested source: [`d020bfd79afed66f4ab981bbc4d5a3ab580e187c`](https://github.com/Livara-Dev/Telegram-iOS/commit/d020bfd79afed66f4ab981bbc4d5a3ab580e187c), tree `3f084dbedf569d39d5c3ba60afc7edc35b764b62`; request `f4476c7c2c3d497539be5a1204cc126807be5acb`. The candidate includes the Account coordinator relocation before managed expiry/deletion subscriptions and the fixture type correction. **The frozen `5d241267735b3958c225681d173853568bab705d` binary does not contain that relocation and is not qualified by this run.** Full Account wiring is compiled/source-reviewed; these fixtures do not instantiate the complete Account initializer. No application or IPA was built in this Core-only run.

The 854 count belongs to this candidate and this Core run. Other records, including 964, remain separate source/scope results; they must not be added into a combined unique release total.

## Exact 16 recipient cases

These incoming local-media fixtures use cloud private chats or secret chats. They call the actual local-consume API and real managed timer/worker, compare complete bytes, use normal cache removal, and close/reopen the archive with capture off. Opt-out and incomplete-source cases check their negative outcomes; they do not claim complete retained bytes.

| Case | Peer fixture | Timer | Media | Checked outcome |
| --- | --- | --- | --- | --- |
| 1 | Cloud private | timed | Photo | Complete bytes retained |
| 2 | Cloud private | timed | Voice | Complete bytes retained |
| 3 | Cloud private | timed | Video | Complete bytes retained |
| 4 | Cloud private | timed | Round video | Complete bytes retained |
| 5 | Cloud private | view once | Photo | Complete bytes retained |
| 6 | Cloud private | view once | Voice | Complete bytes retained |
| 7 | Cloud private | view once | Video | Complete bytes retained |
| 8 | Cloud private | view once | Round video | Complete bytes retained |
| 9 | Cloud private | view once | Photo | Locally armed timestamp catch-up; bytes retained |
| 10 | Secret | timed | Photo | Complete bytes retained |
| 11 | Secret | timed | Voice | Complete bytes retained |
| 12 | Cloud private | timed | Voice | Opt-out: no archive event |
| 13 | Cloud private | view once | Photo | Opt-out: no archive event |
| 14 | Secret | timed | Photo | Opt-out: no archive event |
| 15 | Cloud private | view once | Voice | Incomplete source: honest unavailable media |
| 16 | Secret | timed | Photo | Incomplete source: honest unavailable media |

Cloud cache clearing uses the standard MediaBox removal API after the archive decision; it does not establish survival of eviction before source acquisition. Secret photo/voice cases assert that canonical complete/partial/meta paths are removed while Store work remains held, then verify retained bytes or the expected negative outcome. Payloads are synthetic JPEG, H.264 MP4, Opus/Ogg and H.264/AAC MP4 fixtures. Exact byte equality is checked; native player playback is not.

## Three startup-order cases

All three use a restored, already-due secret-photo message and the real managed worker. The coordinator-first case proves byte retention through canonical cache unlink while Store opening is held, then archive close/reopen. The worker-first control deliberately proves honest missing media after old-order cleanup. The injected `protectionUnavailable` control proves canonical cleanup proceeds without an archive persistence claim. All three check that another account's distinct bytes under the same resource ID remain unchanged. These component contracts support the source-reviewed Account relocation; they do not execute the complete Account initializer or actual iOS Keychain locking.

## All 19 observed test identities

Every identity below was observed once started and once passed in the retained XCTest log. Full module/class/method identities prevent substitution by count alone.

1. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testIncomingTimedPhotoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval` — passed.
2. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testIncomingTimedVoiceUsesLiveWorkerAndPersistsBytesAfterCacheRemoval` — passed.
3. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testIncomingTimedVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval` — passed.
4. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testIncomingTimedRoundVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval` — passed.
5. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testIncomingViewOncePhotoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval` — passed.
6. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testIncomingViewOnceVoiceUsesLiveWorkerAndPersistsBytesAfterCacheRemoval` — passed.
7. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testIncomingViewOnceVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval` — passed.
8. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testIncomingViewOnceRoundVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval` — passed.
9. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testViewOnceWorkerCatchesUpFromActualLocallyArmedTimestamp` — passed.
10. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testSecretTimedPhotoWorkerUnlinksCacheBeforeStoreCanCommit` — passed.
11. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testSecretTimedVoiceWorkerUnlinksCacheBeforeStoreCanCommit` — passed.
12. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testCloudTimedVoiceOptOutDoesNotPreventCanonicalExpiry` — passed.
13. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testCloudViewOncePhotoOptOutDoesNotPreventCanonicalExpiry` — passed.
14. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testSecretTimedPhotoOptOutDoesNotPreventCanonicalCacheRemoval` — passed.
15. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testCloudViewOnceVoiceDoesNotTreatPartialCacheAsRetainedBytes` — passed.
16. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveRecipientExpiryWorkerTests.testSecretTimedPhotoPartialCacheIsRemovedWithoutInventingArchivedBytes` — passed.
17. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveStartupOrderingTests.testCoordinatorSubscriptionBeforeRestoredDueWorkerPinsWhileStoreOpenIsPending` — passed.
18. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveStartupOrderingTests.testRestoredDueWorkerBeforeCoordinatorRecordsHonestMissingMedia` — passed.
19. `submodules_TelegramCore_TelegramCoreTestsLib.LocalArchiveStartupOrderingTests.testProtectedStoreFailureDoesNotDelayRestoredSecretCanonicalExpiry` — passed.

## Artifact integrity

Artifact [13544045782](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42295994007/artifacts/13544045782) contains the ten expected entries. Its ZIP is 96,786 bytes and matches GitHub SHA-256 `b898d0fa4f386d4cc9850a73a9aa6b6eba3863f70f4b571d54311eede1768a6a`. All nine evidence-file hashes match `recipient-SHA256SUMS`; that manifest's SHA-256 is `2e162aff2530875def783174b02ba34da7410e113772e02d03c3cce41ddc62b2`. The successful single XML aggregate embeds the same native log, which is not counted twice. Native XML/test.log each have the existing 2 MiB cap; other uploaded text logs are not byte-capped.

| Evidence path | SHA-256 |
| --- | --- |
| `host.log` | `442e8a53994d0d227d953ddee4b68870b9e0d75c6cf5a87c248e07aa166e895c` |
| `integration-tests.json` | `5376d7b93a516f472be8b455aba973870d1b21b0346459600892dd4c4d8899f6` |
| `native-core.log` | `ec07feaa1ed20d616e9cd3d17940a0504aac3b1937a2283a26a34804edab4da2` |
| `portable-tests.log` | `296629878a87e14a84ab0e8bf10e8dccedb1e836e6718c4dc324da33a754f52c` |
| `prebuild-telegram-core/collection.json` | `3d37bfa430d239de9c86eec7c27e669389aa5a1944e800714d580c95a9183ebb` |
| `prebuild-telegram-core/test.log` | `7a24bf405d0508c7f2040cfc76043e9df4203526027853de013d0be7578a16d5` |
| `prebuild-telegram-core/test.xml` | `2185a49ba1d5b1014347b27b8e391c74ffe4c7bbaf13d7f8c6b12ba8c7ba29d2` |
| `recipient-request.json` | `bf451cd3e694453be2089d7969b5d9283c6e7a59e11cd9ee820df462ee157cbe` |
| `runtime.log` | `dc43d672cd9866374fcecd1301d2b5a74863b082b82e332823af1eea4fd01eb8` |

## Preserved failed attempt

The earlier [851-case candidate attempt 42291375322](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42291375322) failed test compilation before any XCTest ran. Two constructor diagnostics came from one fixture declaration. The one-line correction was `let resource: MediaResource` → `let resource: TelegramMediaResource`; failure artifact `13540804680` remains preserved with ZIP SHA-256 `4f5006466264d9fc42000d8ea3d56c631d03ef4754f67f16e646e251a46bfb93`. That attempt contributes no passing native identities or Core/All-test totals.

## Verification limits

This evidence covers local Core fixtures, actual worker-driven expiry/deletion, cache cleanup, byte preservation and archive close/reopen for the outcomes above. It does not qualify gallery/controller/player callbacks, sender-side remote consumption, native playback, live server delivery/download, group/channel received-media paths, suspension, OS kill/process death or physical-device behavior. Already expired/unreceived content and absent/incomplete bytes are not reconstructed. No API-policy approval is implied.

This publication draft uses only saved native proof. Final IPA/binary provenance and any artifact-only UI recovery evidence must keep their own exact source and scope; no such result is asserted here.
