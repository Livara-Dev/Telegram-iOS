# Preservation priority and current support

Deleted-message/media preservation is the active priority. Existing secondary
features remain intact. The byte-storage increment below is source-implemented;
its native tests and running application have not yet been verified.

| Content/event | Current scope | Evidence |
| --- | --- | --- |
| Ordinary cloud text deletion and edited text | Encrypted independent archive, covered server/local delete and edit paths | 56 earlier archive integration cases passed at 387177fd. |
| Completed local photos, videos, voice, GIF/animation, documents | Utility-queue copy into encrypted SQLite BLOBs; streaming path capped at 512 MiB/file and 10 items/event; in-memory image preview capped at 16 MiB, within configured retention quota | Small-file storage and real coordinator cases passed at 92f6d752; new large-file streaming cases await native CI. |
| Photo variants | Tries up to ten bounded, largest-first resource candidates already in the cache | May preserve a smaller available rendition; no original-resolution guarantee. |
| Incomplete/missing/oversized media | Metadata and caption can survive; missing-file indicator, no silent success | File reads do not start a download. |
| Timed, view-once, Secret Chat or copy-protected content | Excluded | No expiry/encryption/server-access bypass. |
| Image viewing | Local bounded-resolution preview of retained images, without writing plaintext temporary files | Native UI/frames unverified. |
| Video/voice playback | Native AVFoundation entry points implemented in source | Codec, UI and device tests pending; Ogg voice support is not established. |
| Media gallery | Account/chat-scoped encrypted attachment index, 60-item pages, type filters, serial thumbnail reads, attachment opening and message-history navigation | Source authored; native and recorded UI validation pending. Video entries use icons; GIF image preview remains a still frame. |
| Document viewing | Retained bytes and metadata only | No document viewer or plaintext export. |

Schema 5 adds encrypted event-linked media rows. Deletes, retention and explicit
archive purge remove their bytes and adjust quota accounting transactionally.
Older schemas migrate in a transaction; newer schemas remain unsupported.
The normal Telegram cache is never used as the archive's persistent destination.
Only archive DB/WAL files persist, under the existing Keychain key and Data
Protection policy. No networking or third-party uploads were added.

The durable Postbox outbox is acknowledged after the event and eligible media
writes complete. Global server deletions defer eager removal of only those cache
resources referenced by successfully queued, eligible captures. Disabled,
excluded, timed, or failed-to-queue captures retain normal Telegram removal.
This deferral is not a persistent cache pin; ordinary cleanup can still remove
a source before the worker opens it. Repeated media writes must match existing bytes, and generation
checks reject writes after purge. Transient locked-file/I/O failures keep work
pending. Missing, incomplete or oversized files remain explicitly unavailable.

This is still a best-effort capture window: a cache file removed before the
worker opens it cannot be recovered. Proactive capture on receipt/download,
larger streaming media, real process termination, device-lock behavior and
physical storage pressure remain acceptance work. Reopen and simulated failure
tests are not physical crash tests. Nothing here recovers unseen content.

Telegram's published API terms prohibit preventing self-destructing content
from disappearing. Timed/view-once preservation remains unsupported under the
current implementation and contract constraints. Ordinary cached cloud media
is the current target. See BUILD-FAILURES.md for hosted runtime/build blockers.

New accounts default to no age expiry; saved choices for existing accounts are
preserved. A configured payload quota still evicts the oldest records and their
media, and explicit Clear Archive remains destructive. No unbounded or eternal
retention guarantee is made. The native control supports quotas through 1 GiB.

Photo cases use a generated valid JPEG with Telegram's CloudPhotoSizeMediaResource,
including a missing-large/downloaded-small rendition case. Other file types use
synthetic bytes in actual Telegram media objects to check byte-for-byte storage,
not codec playback. Cases include photos, videos, round video, voice, animation and documents; encrypted store reopening, real MediaBox
cache removal, quota eviction, account separation, and the media-commit/outbox-
acknowledgement recovery window. Actual app termination and physical iPhone
installation are not established by these fixtures.

The follow-up handoff regression cases call the real `replayFinalState` global
deletion and edit handlers, drain MediaBox's removal queue, then read retained
bytes after explicitly clearing the cache. These tests are newly authored and
await native execution; they must not be presented as passing. A retention
eviction during media insertion is counted as an unavailable capture rather
than reported as preserved.

Schema-5 storage validation: revision `aba9fae9` passed 59 macOS XCTest cases
and standalone iOS simulator module compilation. [Verified artifact details](evidence/archive-media-aba9fae9.json) identify the revision, workflow, artifact
and SHA-256 digest. This result predates the global-delete handoff and truncated-
file fixes, and does not validate TelegramCore integration, SettingsUI, codec
playback, an application package or physical iPhone behavior.

The same `aba9fae9` revision passed 201 Postbox and 59 archive tests on the iOS
simulator, then failed compiling the TelegramCore media-read signal. No new
TelegramCore media-capture/replay case executed. The explicit weak-capture fix
and release-completion regression are pending a new native run.

Direct media reads now apply configured age retention before returning bytes,
even when the store has stayed open without writes or a list refresh. A
clock-controlled storage test checks deletion of the expired parent and blob
and their usage counters. This follow-up still awaits its native test run.

Schema 6 adds an FTS5 word/phrase index inside the same encrypted database.
The native archive browser can search deleted snapshots and earlier edit
versions, globally or in the current chat. Queries are bounded, quoted as
literals, and paginated; wildcard/regex/substring syntax is not offered. Search
uses SQLite's unicode61 case/diacritic folding. Index updates and retention
cleanup are transactional; temporary query storage is forced to memory. Index
allocation adds physical disk overhead beyond the logical payload quota.
Storage, migration, account-scope and facade tests are authored for this
increment; native execution and recorded UI validation are pending.

The account's archive settings now show snapshot count and logical stored payload
bytes from the database counters. The read uses the same protected, account-scoped
queue and retention gate as browsing. Settings changes refresh usage. These
figures deliberately exclude SQLite/index allocation overhead and must not be
presented as exact disk usage. Native facade and UI verification remain pending.

At `92f6d752`, 1,060 iOS XCTest cases passed across Postbox, archive storage,
TelegramCore and TextFormat, including all 74 archive integration cases. Real
global-delete/photo copying, edit replacement, typed cached-file retention,
cache removal/reopen, quota loss, malformed MIME and acknowledgement recovery
cases passed. [Exact evidence](evidence/archive-media-integration-92f6d752.json)
limits this to local integration: the projection host did not launch, and no
full Telegram app, physical device or codec playback was verified. Later
search/usage changes require their own native results.

## Bounded large-file storage

The schema-7 path streams completed MediaBox files directly into SQLCipher BLOBs
in 64 KiB chunks, with one transaction per attachment. No plaintext archive
staging file or network fetch is created. File type, expected byte count and
modification time are checked. Missing, incomplete, symlink and over-limit files
remain reported gaps. Files that cannot fit the configured quota are skipped
without discarding the message text. I/O errors roll back the attachment and
leave its durable outbox entry pending. Account generation checks and existing
commit-before-ack recovery apply to this path.

The hard source limit is 512 MiB per attachment, still subject to the account's
quota and available disk space; encryption and WAL can temporarily consume more
disk than the logical quota. Metadata lookup and reads of up to 1 MiB support
future players/exporters without loading the whole attachment. The existing
image preview accepts at most 16 MiB and is not a video/document player.
New 20 MiB storage and real Postbox coordinator tests are authored, including
cache removal and reopen. They have not yet passed native CI. No device
performance or physical-iPhone playback result is claimed.

The schema-6 search revision e690da94 passed 64 macOS archive tests and iOS
simulator module compilation in run 41849015889. That is not full application
qualification and does not qualify subsequent source revisions automatically.

## Native playback work (not yet runtime verified)

A separate first-party `LocalArchivePlayback` module now feeds AVFoundation from
64 KiB encrypted-store reads through a private URL scheme. It writes no plaintext
media file and contains no HTTP client. Native archive-version rows open an
embedded iOS player for video, round-video and voice entries. Actual codec support
is whatever AVFoundation can decode; Ogg/Opus voice support is not established,
and documents do not yet have a reader/export UI. Image/animation entries retain
the bounded first-frame preview. This is not a finished media gallery.

Tests use a generated 16×16 H.264 MP4 fixture: store encrypted bytes, remove the
source, reopen the database, load AVAsset metadata and decode a frame. Missing
metadata/bytes and cancellation have separate cases. Native results are pending;
authenticated Telegram conversations and physical-device playback remain untested.

Before UI verification, expected frames are: version row opens a paused player
below the navigation bar; transport controls remain visible and tappable in both
orientations; back closes playback; background/lock/purge clears decoded content
and requires reopening. Image previews also clear on background/lock. No recorded
simulator frames exist yet, so layout and animation quality are not verified.


## Gallery increment awaiting native verification

Schema 8 adds an encrypted index of retained attachment slots. Its rows are
created in the same transaction as the attachment and removed on retention or
purge; a transactional migration backfills existing bytes. Pages use a
sequence/slot cursor and never load BLOB contents just to list media. The native
screen opens from archive settings, optionally scoped to the current chat, and
can navigate to a selected attachment's message history. Only completed stored
attachments appear; missing downloads are not presented as available media.

Thumbnail reads and ImageIO decoding are serialized on a utility queue. Each
cell discards its result on reuse, and background/locked state clears displayed
images and metadata. Images retain the 16 MiB preview limit and bounded pixel
checks. No plaintext file or network request is introduced. Video, round video
and voice use the existing pending-validation AVFoundation reader; documents
show metadata. Large galleries retain one bounded page plus numeric cursors.

New native tests cover paged slots, account/chat/type scoping, schema-7 migration
and rollback, retention, conflicting writes, real Postbox gallery projection,
and refusal of locked-account queries. These tests are authored, not yet
reported as passed. Linux source/tooling checks cannot validate Swift UI or
substitute for recorded simulator frames and device testing.
