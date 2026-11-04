# LocalMessageArchive

First-party, opt-in local preservation for this Telegram-iOS fork. The module
uses the repository's pinned SQLCipher library and an independent Keychain key
for each Telegram account record. It contains no Telegram authentication,
MTProto transport or upload client.

The current source is schema 7. Large-file streaming and native media playback
are implemented in source and await native validation. They are not certified
as a working complete application.

## Application integration

`TelegramCore/Sources/LocalArchive` owns capture policy, the durable Postbox
outbox and the account coordinator. Covered delete/edit handlers snapshot
eligible, already-received cloud messages before canonical mutation. The worker
commits the event, attempts its completed local media, and only then acknowledges
the outbox item. A persisted receipt makes replay after archive commit / before
Postbox acknowledgement idempotent. Explicit purge advances a generation so old
queued captures cannot repopulate a cleared archive.

The archive directory is separate from MediaBox. Normal Telegram media-cache
cleanup therefore does not remove a committed archive copy. A source file can
still disappear before capture opens it; missing/incomplete content is reported
as a gap, not downloaded or described as recovered. Some range/history-reset
mutation paths remain uncovered. Messages never received cannot be recovered.

Native settings and conversation/version entry points live in
`SettingsUI/Sources/LocalArchiveSettingsController.swift`. Telegram's existing
authentication and networking remain outside the archive.

## Storage and read APIs

All database operations execute on the store's serial worker. Completions run on
the chosen callback queue. Never block the main thread waiting for a completion.

| API | Contract |
| --- | --- |
| `open` | Probe/create the account key as appropriate, authenticate existing pages, validate identity, migrate transactionally and enforce retention. |
| `record` / `recordFromOutbox` | Store immutable deleted/edited snapshots with bound SQL; reject conflicting reuse and stale purge generations. |
| `recordMedia` | Convenience API for a bounded in-memory payload, at most 16 MiB. |
| `recordMediaFile` | Stream a completed local file into SQLCipher in 64 KiB chunks, at most 512 MiB and within the account quota. Verify regular file, advertised size when present, and size/mtime consistency. Reject symlinks and incomplete/empty media; empty documents are valid. |
| `mediaInfo` / `mediaRange` | Read metadata or at most 1 MiB of bytes. A range never materializes the whole attachment. |
| `media` | Bounded in-memory preview, at most 16 MiB; check size before selecting its BLOB. |
| `deletedMessages` / `timeline` | Bounded pages by account, peer/message and capture sequence. Keep server edit time separate from local observation time. |
| `searchMessages` | Account-local FTS5 word/phrase search with a bound, phrase-quoted query; no network search, regex or substring guarantee. |
| `setRetention` / `usage` | Configure age/size policy and read transactional logical-byte/snapshot counters. |
| `purge` | Atomically remove events/media and advance the capture generation. |

A streaming attachment that cannot fit together with its text and previously
saved attachments is skipped without discarding those saved portions. I/O
failure rolls back the entire new attachment. An already-committed slot is
immutable; an exact retry is accepted, different bytes are rejected.

## Encryption, retention and recovery

Production keys use `WhenUnlockedThisDeviceOnly` Keychain access, are not
synchronizable, and are never derived from Telegram session/API material.
Database files use iOS Data Protection and backup exclusion. SQLCipher encrypts
pages, journals and the FTS index. Temporary SQL storage is configured in memory.
Purge/retention delete the media rows and update usage counters in the same
transaction as their parent events.

The coordinator closes storage when protected data becomes unavailable and
requires a fresh key lookup to reopen. Initial notification setup permits a key
probe; it is not an assertion that the device is unlocked. A dedicated iOS test
posts UIKit's real notification constants through this production bridge; native
execution of that new test is pending.

Retention applies on opening, writing and reading content, and when changing
policy. Logical quota includes text/entity/event-key/record and media costs. It
does **not** cap physical SQLite/WAL/index allocation. A migration, a large
transaction or free pages can require additional disk space. Age/size retention
can intentionally remove records; no-age-expiry does not mean unlimited space.

Schema upgrades are transactional. Schema 6 added encrypted FTS5 search;
schema 7 increases the media limit without changing existing event keys,
attachment bytes or usage counters. Unknown future schemas fail closed.
Missing keys never cause an existing encrypted database to be replaced.

## Native playback

`LocalArchivePlayback` is a separate first-party target using Apple AVFoundation.
It has a cancellable source interface and reads at most 64 KiB per request step,
with at most eight active requests. The application supplies account-gated
archive ranges. A private URL scheme identifies the asset; no decrypted
staging file is written. External AVFoundation data references are forbidden.

The native player still needs codec, UI, account-switch and device verification.
Ogg voice support is not established, GIF previews show a still frame, documents
have no viewer/export interface, and a complete gallery is not implemented.

## Verification and open limits

On a compatible Mac:

```sh
swift test --package-path submodules/LocalMessageArchive
```

The application workflow additionally uses these explicit iOS targets:

- `//submodules/LocalMessageArchive:LocalMessageArchiveTests`
- `//submodules/LocalMessageArchive:LocalArchivePlaybackTests`
- `//submodules/TelegramCore:TelegramCoreTests`

A simulator module build is not a runtime test. Storage/codec tests are not proof
of authenticated Telegram conversations, a complete app, an independently
signed IPA or physical-iPhone behavior.

Historical evidence: revision `e690da94` passed 64 macOS archive tests and iOS
module compilation; revision `92f6d752` passed 1,060 iOS cases across four suites,
including 74 archive integration cases. Exact run/artifact hashes are in
`docs/fork/evidence`. Those results do not qualify subsequent streaming/playback
changes. New tests cover 20 MiB source removal/reopen, range integrity, rollback,
quota boundaries, empty/incomplete files, a generated H.264 decode, cancellation
and UIKit notification wiring; their native results are pending.

Secret, disappearing/view-once, timed and copy-protected content remains excluded.
There is no encrypted import/export or synchronization yet. Postbox disk-full
commit semantics, simultaneous source-cache removal, app termination during
handoff, full UI behavior and iPhone 15 Pro performance remain acceptance risks.
See `docs/fork/PRESERVATION-STATUS.md` and `docs/SECURITY_ASSESSMENT.md`.
