# LocalMessageArchive — unintegrated draft

This native Swift module stores supplied text and entity metadata in a separate
SQLCipher database. It is **not connected to Telegram update processing**. It does
not yet archive real incoming messages, preserve media, modify search, show UI,
export/import records, or synchronize anything. Compilation and tests must be
confirmed by the hosted macOS workflow before reporting this increment as tested.

## Storage and security contract

- Call `ArchiveStore.open` with a dedicated, archive-owned app-container directory
  outside Telegram's cache. The module changes that directory's permissions,
  backup exclusion and iOS Data Protection class. Do not pass a shared container
  root or any Telegram database/cache directory.
- Each `account-<recordId>/archive.sqlite` has an independent 32-byte key supplied
  by `ArchiveKeyProvider`. The production provider uses non-synchronizable,
  `WhenUnlockedThisDeviceOnly` Keychain items. Existing files always request an
  existing key; missing, invalid or inaccessible keys fail closed.
- SQLCipher is the repository's existing local dependency. Each connection checks
  cipher availability and sets `cipher_plaintext_header_size=0` **after** setting
  its key, overriding Postbox's process default without changing that default.
  Account identity is also stored inside the encrypted database and verified.
- Dedicated directories use mode 0700; database, WAL, SHM and rollback-journal
  endpoints use 0600. iOS uses complete file protection; backup exclusion is
  applied to the directory and existing files. Sidecars inherit the protected
  directory policy. Failure to apply policy prevents opening the store. Endpoint
  symlinks and orphan sidecars are rejected. A canonical parent path accommodates
  system aliases; SQLite also opens with `SQLITE_OPEN_NOFOLLOW`.
- No network API, telemetry, authentication material, debug payload logging or
  new dependency is introduced. Errors contain fixed cases or numeric SQLite /
  Keychain status codes. Swift `Data`/String copies and an open SQLCipher handle
  can hold plaintext/key material in memory: no zeroization or immediate device
  lock memory-erasure guarantee is made. Device lock/backup behavior still needs
  iOS integration and device tests.

## API and schema invariants

Opening, Keychain access, SQL, and retention run on a private serial utility queue.
Completions use the supplied callback queue (main by default). Callers must await
successful `record` completion before treating a snapshot as durable. The future
Postbox adapter needs a durable capture/outbox and deletion barrier; independent
databases do not offer a shared transaction. No hook is installed by this draft.

Schema version 1 stores account metadata and events with a unique event key,
account/peer/namespace/message identity, previous text/entity metadata, event kind,
local observation time, optional server edit time, and monotonically increasing
local sequence. Timeline pages use capture sequence, not an assumed server order.
Only empty version-0 databases can initialize. Unknown populated version-0 or
future schemas fail without schema mutation; no future migration is invented.

The future adapter must supply stable event keys and original observation times
on retries, and classify cloud/secret/expiring/view-once content explicitly.
Capture is disabled by default and unclassified, secret, expiring, and view-once
messages are excluded. Bot conversations are excluded by default; peer and sender
exclusions are supported. No messages can be recovered before capture.

An identical event retry is a no-op while that event exists. Reusing its key with
different data fails and rolls back the whole batch. There are no replay tombstones
after retention/purge yet: the future adapter must add replay/purge coordination
before integration. A replay with the same original expired time is immediately
pruned; a caller that invents a new time can reintroduce deleted data.

SQL input is bound. Limits are 256 events / 8 MiB per batch, 256 KiB UTF-8 text,
64 KiB entities, 128 UTF-8 bytes per event key, and 200 records per result page.
Account mismatch or invalid input aborts the batch, including excluded inputs.
Entity bytes are opaque and need a versioned Telegram adapter codec before use.

## Retention and performance limits

Retention executes on successful open, after each record batch, and on
`setRetention`. Reads do not schedule time-based deletion: an idle open store
needs future app lifecycle/timer maintenance. Age uses the original local
observation time; records exactly on the age boundary are retained.

The quota counts UTF-8 text + entity bytes + UTF-8 event-key bytes + a 64-byte
metadata allowance. It is a **logical payload quota**, not measured physical disk
usage: indexes, SQLite free pages and WAL can consume additional space. Newest
events by observation time/sequence are retained until the cumulative quota is
exceeded; an individual event larger than the quota is dropped. The implementation
has no media/file quota, physical-space monitor or compaction scheduler yet.
Retention scans use SQLite window aggregation rather than loading the history
into Swift. Large-history runtime, memory, battery and disk behavior have not been
benchmarked; no iPhone 15 Pro performance claim is made.

## Validation

On a compatible Mac with the local `sqlcipher` submodule initialized:

```sh
swift test --package-path submodules/LocalMessageArchive
```

The explicit Bazel iOS test target is
`//submodules/LocalMessageArchive:LocalMessageArchiveTests`; use the repository's
configured Make.py/Bazel environment. `archive-tests.yml` runs the standalone
macOS package. These are storage/Keychain boundary tests, not Telegram app tests.
The test suite covers encryption/header defaults, wrong keys, key lifecycle,
account identity, edits/deletions, rollback/idempotency, policy exclusions,
retention/reopen, pagination/binding, future schema rejection, and unsafe paths.
Real Keychain accessibility, interruption/storage exhaustion, Telegram deletion
ordering, media and foreground/background behavior remain integration work.
