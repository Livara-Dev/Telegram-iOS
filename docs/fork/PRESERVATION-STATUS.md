# Preservation priority and current support

Deleted-message/media preservation is the active priority. Existing secondary
features remain intact. The byte-storage increment below is source-implemented;
its native tests and running application have not yet been verified.

| Content/event | Current scope | Evidence |
| --- | --- | --- |
| Ordinary cloud text deletion and edited text | Encrypted independent archive, covered server/local delete and edit paths | 56 earlier archive integration cases passed at 387177fd. |
| Completed local photos, videos, voice, GIF/animation, documents | Utility-queue copy into encrypted SQLite BLOBs; maximum 16 MiB/file and 10 items/event, within configured retention quota | New native storage/coordinator tests authored; not yet passed. |
| Photo variants | Tries up to ten bounded, largest-first resource candidates already in the cache | May preserve a smaller available rendition; no original-resolution guarantee. |
| Incomplete/missing/oversized media | Metadata and caption can survive; missing-file indicator, no silent success | File reads do not start a download. |
| Timed, view-once, Secret Chat or copy-protected content | Excluded | No expiry/encryption/server-access bypass. |
| Image viewing | Local bounded-resolution preview of retained images, without writing plaintext temporary files | Native UI/frames unverified. |
| Video/voice/document playback and media search | Not implemented | Stored bytes alone are not a complete gallery/player. |

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
