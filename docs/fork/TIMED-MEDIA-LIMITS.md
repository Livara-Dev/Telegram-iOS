# Timed and view-once media: actual behavior

Audit date: 2026-11-05. Application source inspected at `73b52244`, whose
application payload is unchanged from `62546557`. The new native information
page is source authored; native compilation, visual checks and physical-device
verification must be recorded separately. No timer, receipt, download, encryption
or expiry behavior was changed by this audit/page.

## Why timed-photo preservation currently does nothing

There is no timed-photo preservation preference in this iOS implementation.
The ordinary archive switch controls eligible deletion/edit snapshots. The
adapter rejects messages carrying either `AutoremoveTimeoutMessageAttribute`
or `AutoclearTimeoutMessageAttribute` before creating an archive operation.
That includes a photo whose bytes are already locally accessible. The archive
worker never receives that photo, so neither the storage engine nor its media
player can preserve it. This is an intentional exclusion, not successful timed
preservation or a recovery feature.

Telegram's current [API Terms of Service, section 1.4](https://core.telegram.org/api/terms)
prohibit preventing self-destructing content from disappearing. No contractually
permitted preservation exception has been established for the expiring types
below. Local accessibility does not establish permission to retain the content.
The user requested preservation only where technically and contractually
permitted; this implementation consequently keeps these exclusions and offers
no misleading preservation switch.

## Separate capability results

| Type | Receive/playback path | Archive behavior | Evidence and limits |
| --- | --- | --- | --- |
| Self-destructing photo | API photo `ttlSeconds` becomes an autoclear attribute; the normal secret-media gallery can display accessible bytes. | Excluded before archive outbox insertion. | Source audited; generic timer exclusion is covered by existing native integration tests. No preservation success is claimed. |
| Self-destructing video | API document `ttlSeconds` becomes an autoclear attribute; the normal gallery uses the video duration/timer. | Excluded even when its cache file is complete. | Existing channel-range/global-delete tests exercise a timer-bearing synthetic file and preserve canonical cache removal; they do not prove decoded timed-video behavior. |
| View-once photo | `viewOnceTimeout` is `Int32.max`; an autoclear timestamp is scheduled at consumption. | Excluded before outbox insertion and not made replayable. | Generic view-once attribute exclusion is tested; no physical view-once-photo run is established. |
| View-once video | Uses the same sentinel plus the normal video gallery's one-time playback/dismissal logic. | Excluded, including any accessible complete file. | Source audited separately from photos; no physical view-once-video run is established. |
| Disappearing voice message | File attributes identify voice; its autoclear/autoremove attribute starts consumption/expiry behavior. | Excluded. An ordinary, non-expiring voice message is a separate eligible type. | Existing general timer guards apply; no disappearing-voice preservation or device test is claimed. |
| Disappearing round video message | File attributes identify instant video; view-once playback uses the ordinary instant-video/secret-media UI. | Excluded. Ordinary non-expiring round video remains a separate eligible type. | Source audited; no disappearing-round-video preservation/device test is claimed. |
| Chat auto-delete messages | Message `ttlPeriod` creates an autoremove attribute; the scheduled removal worker performs canonical deletion. | Excluded, including text or media with longer timers. | Existing capture tests include a 3,600-second autoremove message. No permitted policy exception is assumed. |
| Secret Chats | Existing decrypted-operation, encrypted-resource and secret-chat consumption paths remain upstream. | All Secret Chats excluded, including messages without a timer. | The adapter requires an ordinary cloud peer/message namespace; no decryption bypass is added. |
| Already expired content | API placeholders become `TelegramMediaExpiredContent`. | Excluded and not recoverable. | A placeholder does not supply the missing original bytes. |
| Ordinary non-expiring cloud media | Existing Telegram receipt/download/playback; the archive receives an eligible deletion/edit operation. | Completed local bytes may be copied into independent encrypted storage within configured limits. | Separate ordinary capture/retention tests exist. This is not proof of every live server/UI media scenario. |

## iOS receipt, availability and expiry trace

1. [StoreMessage_Telegram.swift](../../submodules/TelegramCore/Sources/ApiUtils/StoreMessage_Telegram.swift)
   parses photo/document `ttlSeconds` in `textMediaAndExpirationTimerFromApiMedia`
   (around lines 630–675) and installs `AutoclearTimeoutMessageAttribute` when
   constructing messages (around lines 519 and 1274). Message `ttlPeriod`
   installs `AutoremoveTimeoutMessageAttribute` around lines 1307 and 1649.
2. [SyncCore_AutoremoveTimeoutMessageAttribute.swift](../../submodules/TelegramCore/Sources/SyncCore/SyncCore_AutoremoveTimeoutMessageAttribute.swift)
   defines the view-once sentinel and timestamp tags. Autoclear view-once content
   schedules tag 1 at the countdown's beginning; ordinary autoclear schedules it
   at the expiry timestamp. `containsSecretMedia` classifies relevant local UI
   behavior; it is not an archive authorization check.
3. [SecretMediaPreviewController.swift](../../submodules/GalleryUI/Sources/SecretMediaPreviewController.swift)
   recognizes the sentinel around lines 327/340 and invokes the engine's normal
   content-consumption operation around line 552. Video timing and one-time
   playback/dismissal remain intact.
4. [MarkMessageContentAsConsumedInteractively.swift](../../submodules/TelegramCore/Sources/TelegramEngine/Messages/MarkMessageContentAsConsumedInteractively.swift)
   initializes timers while updating local consumed state. Its remote-consumption
   path can immediately replace view-once/expired photo, file, voice or round-video
   media with `TelegramMediaExpiredContent`. A future Ghost change must not skip
   this local path merely to gate outbound receipts: that could disable expiry.
5. [ManagedAutoremoveMessageOperations.swift](../../submodules/TelegramCore/Sources/State/ManagedAutoremoveMessageOperations.swift)
   uses timestamp-based attributes. It deletes Secret Chat/autoremove messages,
   or replaces autoclear media with expired-content placeholders. It contains
   no archive-preservation hook.
6. [LocalArchiveCapture.swift](../../submodules/TelegramCore/Sources/LocalArchive/LocalArchiveCapture.swift)
   rejects all timer attributes, Secret Chat/local/scheduled namespaces,
   copy-protected messages/peers and expired media. Its deletion collector can
   defer eager cache unlink only after a successful eligible ordinary capture.
7. [LocalArchiveCoordinator.swift](../../submodules/TelegramCore/Sources/LocalArchive/LocalArchiveCoordinator.swift)
   resolves only MediaBox's completed resource path and passes it to the archive
   worker. It does not initiate a download. Partial/missing files can remain
   unavailable, and normal cleanup can race a future capture. Timed messages
   never reach this worker through the current adapter.
8. [ArchiveStore.swift](../../submodules/LocalMessageArchive/Sources/LocalMessageArchive/ArchiveStore.swift)
   streams validated completed files into encrypted storage. It bounds file size,
   rejects links/nonregular or changing/incomplete files and enforces archive
   storage/retention policy. This is an ordinary-media storage primitive, not a
   timed-media interception mechanism.

## Android reference boundary

The public Android snapshot at `/workspace/AyuGram4A` contains direct TTL hooks
in `MessagesController.java`: deletion handling around 6110–6160 forwards
Secret Chat/TTL messages to `AyuMessagesController.onMessageDeleted`, and the
secret-chat read method around 10586 skips a normal timer task when saving is
enabled. `AyuUtils.isMediaDownloadable` around line 273 includes secret media.
These behaviors were inspected rather than translated. They are not reproduced
in iOS because they interfere with the disappearing-content contract.

The first-party [Android saving documentation](https://docs.ayugram.one/android/saving/)
describes independent Room storage and attempted media downloads. It does not
resolve the API-policy conflict. A later proprietary Android release cannot be
assumed equivalent to this older public implementation; the release audit and
feature matrix track that provenance separately.

## Existing test evidence and remaining checks

- `LocalArchiveCaptureTests.testDisappearingAndViewOnceMessagesAreExcluded`
  verifies timer/view-once attributes produce no archive outbox record while
  canonical deletion proceeds. Its messages are generic fixtures, not separate
  decoded timed photos/videos/voice media.
- `LocalArchiveReplayTests.testChannelRangeRemovalKeepsTimedMediaExclusionAndNormalCacheRemoval`
  verifies a timer-bearing synthetic video-file resource is removed and is not
  deferred into the archive on range deletion.
- `LocalArchiveReplayTests.testGlobalDeleteStillRemovesExcludedAndExpiringMedia`
  verifies normal global deletion/cache removal for an expiring synthetic file.
- `ArchiveStoreTests.testPolicyDefaultsAndSensitiveContentAreExcluded` verifies
  the independent store's expiring/view-once/Secret Chat guards; this is a storage
  boundary test, not interception or timed-media playback.

The new `LocalArchiveTimedMediaExclusionTests.swift` adds **11 authored native
tests; execution is pending**. Separate tests exercise timed photos, timed video,
view-once photos, view-once video, timed voice, timed round video, view-once voice
and view-once round video. They place completed owned media in the real MediaBox,
invoke the production interactive-deletion and remote-consumption transaction
helpers, then reopen Postbox and clear the cache. They require no archive outbox
record, normal consumed/expired state, and no restored original media. Video and
voice payloads are synthetic bytes, not decoded playback fixtures. Further tests
check a persisted chat auto-delete timer, a positive ordinary-photo capture, and
the real local content-consumption function starting a photo countdown. The
latter protects against accidentally skipping expiry while suppressing a read
receipt. These are local transaction tests, not a logged-in server/UI session.

The native Core suite passed at the earlier application source. The new
information page still needs current native compilation, recorded visual review
and physical-device checks. No live timed-media preservation, expired-media
recovery, server replay, complete per-type playback or device protection result
is established by these tests.

## Native settings behavior

`localTimedMediaInformationController(context:)` is the dashboard's Timed Media
destination. It distinguishes every unsupported media type, states the policy
and technical limits, and links to the real ordinary-message archive settings.
It has no preservation toggle or persisted preference because no supported
timed-preservation behavior exists to configure or reset. Ordinary archive
settings still apply immediately through their existing account-scoped worker.
