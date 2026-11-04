# LocalMessageArchive — unintegrated draft

This native Swift module stores supplied text and entity metadata in a separate
SQLCipher database. It is **not connected to Telegram update processing**. It does
not yet archive real incoming messages, preserve media, modify search, show UI,
export/import records, or synchronize anything. Schema-3 commit `c326364b` passed
46 native macOS tests (35 storage including the benchmark, 11 mocked Keychain)
and iOS simulator compilation in [run 41796701379](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41796701379).
This does not establish iOS runtime or Telegram integration behavior.

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
  new dependency is introduced. Errors contain fixed cases, numeric SQLite /
  Keychain status codes, or capture-generation numbers. Swift `Data`/String copies and an open SQLCipher handle
  can hold plaintext/key material in memory: no zeroization or immediate device
  lock memory-erasure guarantee is made. Device lock/backup behavior still needs
  iOS integration and device tests.

## API and schema invariants

Opening, Keychain access, SQL, and retention run on a private serial utility queue.
Completions use the supplied callback queue (main by default). Callers must await
successful `record` completion before treating a snapshot as durable. The future
Postbox adapter needs a durable capture/outbox and deletion barrier; independent
databases do not offer a shared transaction. No hook is installed by this draft.

Schema version 4 preserves the version-1 event layout: a unique event key,
account/peer/namespace/message identity, previous text/entity metadata, event kind,
local observation time, optional server edit time, and monotonically increasing
local sequence. Timeline pages use capture sequence, not an assumed server order.
Version 2 added a database-resident usage row with record and logical-payload counters.
INSERT/DELETE triggers maintain counters in the same transaction as event writes.
Events are immutable: a trigger rejects UPDATE, including payload-size updates.
The API uses ordinary INSERT/DELETE only; direct external SQL, REPLACE and disabled
triggers are unsupported. No in-memory cache of usage can become stale across
store connections.

Only empty version-0 databases can initialize. Version 1 migrates under an
IMMEDIATE transaction, validating account identity and stored payload sizes,
initializing counters from a single snapshot, installing triggers and setting
version 2 atomically. Negative/non-integer/inconsistent legacy sizes are rejected.
Version 3 adds a nonnegative integer capture generation to the account metadata,
initially zero. All required migrations and version updates share the transaction.
Opening rechecks the version after acquiring the write lock. Unknown populated
version-0 or future schemas fail without schema mutation. The counter table rejects
negative values and integer overflow converted by SQLite to REAL.

The future adapter must supply stable event keys and original observation times
on retries, and classify cloud/secret/expiring/view-once content explicitly.
Capture is disabled by default and unclassified, secret, expiring, and view-once
messages are excluded. Bot conversations are excluded by default; peer and sender
exclusions are supported. No messages can be recovered before capture.

For generic `record` calls, an identical event retry is a no-op while that event
exists. Reusing its key with different data fails and rolls back the whole batch.
Generic calls do not carry durable replay receipts after retention.

The application outbox uses `recordFromOutbox` instead. Version 4 adds one
account/generation-scoped high-water index and its last event key, committed in
the same transaction as the snapshot and retention. The caller must supply
monotonically increasing indices from a **single ordered outbox**, process them
in ascending order, and never reset/reuse them within a generation. Skipped
indices are permitted (for example when a capture was rejected before enqueue).
Entries at/below the receipt are consumed without reinsertion even when their
snapshot was evicted. The current boundary detects a different event key; while
an event still exists, conflicting content is rejected too. Older evicted payload
identity cannot be reconstructed from this bounded receipt, so these ordering
requirements are essential. Policy-excluded events advance the receipt too,
preventing later policy changes from resurrecting an already-consumed item.
Version-4 migration initializes an empty receipt without changing history or
generation; it cannot retroactively deduplicate old generic calls.

`record` requires an explicit generation and checks it against the persisted value
inside the same write transaction as capture. Stale and future generations are
rejected. `currentGeneration` exposes the current token. `purge(advancingToGeneration:)`
accepts exactly the next generation, clearing events, resetting the outbox receipt
and advancing metadata
atomically. Retrying the current generation is a no-op, preserving records captured
after the completed purge. Older targets, skipped generations and counter exhaustion
fail without deletion. Generations survive reopen and are isolated by account.

This is only a storage-side purge barrier. A future durable Postbox coordinator
must stamp each capture/outbox item with its generation **when captured**, preserve
that immutable value on retries, and serialize purge intent with capture. Reading
the latest generation while draining old items would defeat the barrier. No such
coordinator is implemented; full application purge safety is not claimed.

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
Version-2 retention uses the observation-time/sequence index and reads or deletes
at most 256 oldest rows per chunk. Quota deletion stops once the required excess
is removed, preserving the newest contiguous set of events. Normal append/usage
operations no longer aggregate the entire history; migration computes totals once.
All chunks still run in one transaction, so a very large expiry/purge can hold the
writer lock and delay completion. Bounded chunks limit temporary row data, not
total work or disk allocation. Other writers can encounter SQLite busy errors.

The test suite records monotonic async single-append timings after 10,000 synthetic
snapshots, including XCTest expectation/wait overhead. It has no latency threshold.
See [measurement evidence](../../docs/fork/PERFORMANCE.md) for schema-1/schema-2
hosted runs; schema 3 measured 0.394 ms mean in the same synthetic append workload. Memory, battery, physical disk
behavior and iPhone 15 Pro performance remain unmeasured.

## Validation

On a compatible Mac with the local `sqlcipher` submodule initialized:

```sh
swift test --package-path submodules/LocalMessageArchive
```

The explicit Bazel iOS test target is
`//submodules/LocalMessageArchive:LocalMessageArchiveTests`; use the repository's
configured Make.py/Bazel environment. `archive-tests.yml` runs the standalone
macOS package, then cross-compiles the module against the selected iOS simulator
SDK to check the `os(iOS)` code path. Cross-compilation is not an iOS runtime test.
These are storage/Keychain boundary checks, not Telegram app tests.
The test suite covers encryption/header defaults, wrong keys, key lifecycle,
account identity, edits/deletions, rollback/idempotency, policy exclusions,
retention/reopen, pagination/binding, future schema rejection, and unsafe paths.
Added schema-2 tests exercise a genuine encrypted version-1 fixture, partial-DDL
migration rollback/retry, invalid legacy rows, immutable events, counters across
two live connections, and expiry/quota deletion crossing chunk boundaries. The
two-connection test alternates operations; simultaneous migration races are not
claimed as tested.
Added schema-3 tests use a genuine encrypted version-2 fixture and exercise stale/
future generation rejection, same-generation purge retry, reopening/two handles,
account isolation, fault rollback after event deletion, integer exhaustion, and
a queued writer/purge boundary. They do not exercise a Telegram/Postbox outbox.
Real Keychain accessibility, interruption/storage exhaustion, Telegram deletion
ordering, media and foreground/background behavior remain integration work.

Schema-4 tests cover an encrypted version-3 migration, partial migration rollback,
replay after quota/age retention and reopening, injected receipt failure rolling
back insertion/retention/counters together, policy changes, shared handles,
account/generation isolation and malformed cursor/boundary input. These tests
require a new native CI result; the schema-3 result cited above does not validate
these later changes. Library receipt tests do not establish the Postbox caller's
commit durability or application lifecycle behavior.
