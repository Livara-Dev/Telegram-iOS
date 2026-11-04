# Preservation priority and current support

Deleted-message and media preservation is the active development priority.
Existing Ghost/filter work is retained without adding secondary features.
This checkpoint is a capture-boundary increment, not completed media retention.

| Content/event | Current implementation | Verified evidence |
| --- | --- | --- |
| Ordinary cloud text deletion and prior edited text | Independent encrypted archive; capture before covered server/local deletion and edit paths; restart, retention and cache separation | 56 existing archive integration cases passed at 387177fd. This is local Postbox/replay evidence, not a live-account test. |
| Photo/file captions | Captured text; no file bytes retained | Earlier capture paths; new metadata tests require native execution. |
| Photo, video, voice, round video, GIF/animation and document metadata | Bounded typed descriptors identify media and explicitly record bytesPreserved=false; attachment-only changes create prior versions | Four new real Postbox/coordinator tests authored plus an extended interactive group-deletion test. Not yet executed natively. |
| Photo/video/voice/GIF/document file bytes | Not implemented | No original media file is claimed recoverable from this checkpoint. |
| Expiring/view-once/Secret Chat content | Excluded from capture | Existing exclusion tests; no permitted retention mode established. |
| Media gallery, media search, encrypted media export | Not implemented | No result claimed. |

The native archive shows an explicit missing-file notice for new media records.
Its missed-capture counter includes events whose media bytes were not saved.
Caption or metadata retention must never be presented as a complete media copy.
The new entity envelope is version 2; version 1 text records remain readable.
Older clients may leave new outbox envelopes unread rather than silently
misinterpret them. Account keys and database schema are unchanged.

Next work is independent encrypted byte storage and a durable copy pipeline for
already downloaded ordinary cloud media. It must distinguish complete,
incomplete, missing and oversized resources, retain data before cache eviction,
and test actual deletion, restart and cache clearing. Metadata alone is not an
adequate substitute for that work. No redownload or recovery of unseen data is
promised, and no archive upload endpoint is present.

Telegram's published API terms prohibit preventing self-destructing content
from disappearing. No implementation here bypasses encryption, protected
content flags, expiry or server access restrictions. Timed/view-once scenarios
remain unsupported under the current implementation and contract constraints;
normal cloud media and its cache-independent retention are the next target.
