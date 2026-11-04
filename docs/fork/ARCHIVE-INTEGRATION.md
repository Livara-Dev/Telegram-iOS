# Experimental application archive integration

This increment connects the native storage library to TelegramCore and SettingsUI.
It is **default off and not yet verified in the running Telegram application**.
The previous exact PR head `663d6ab5` passed 46 macOS library tests and an iOS
module compile in [run 41797053561](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41797053561).
Those results do not cover the new integration or schema-4 receipt.

## Capture and delivery

`AccountStateManagementUtils.replayFinalState` calls the capture adapter before
global-ID deletion, explicit server deletion, and replacement of an edited
message. It only copies an already-present ordinary cloud message's text,
versioned text entities, identity and timestamps. Secret Chats, expiring and
view-once messages, expired/service content and copy-protected content are
excluded. Unknown conversation peers are excluded. Media files are not copied;
media-only edits are not text versions. Pure reaction/count/edit-date changes
do not create a version.

The adapter appends to Postbox's existing operation log in the same transaction
as the canonical mutation. Tag 240 is reserved for this local worker, under the
account's own peer ID. Payloads carry a UUID, capture generation and original
observation time. The bounded queue holds at most 256 entries / 8 MiB encoded
payload. Full queues increment a persistent dropped-capture counter; Telegram
message processing continues. Missing messages cannot be recovered.

The account-owned coordinator uses operation-log views only to wake its utility
queue. It rereads a single pending entry through a fresh Postbox transaction,
then writes it asynchronously to the independent SQLCipher store. It removes
the exact index/UUID only after archive success. Failed writes remain pending;
retries are delayed. Unknown payload versions block the worker rather than
silently acknowledging data.

Schema 4 adds one durable outbox high-water index and last event key per archive.
They commit atomically with the snapshot and retention changes. This prevents
replaying an evicted snapshot after a crash between archive commit and Postbox
acknowledgement. It requires ascending delivery from one account/tag and no
reuse/reset of local indices within a generation. Postbox's existing index
allocation is preserved. The generic library `record` API has no such receipt;
the application worker uses `recordFromOutbox`.

Purge first persists a new generation and pending marker in Postbox. Captures
pause while that marker exists. The worker advances the archive generation and
purges in one archive transaction, then clears the old outbox and pending marker.
Same-generation retries do not delete newly stored rows. This protocol still
needs abrupt-process-termination and real storage-failure tests.

## Native interface and local lifecycle

Settings → Data and Storage → Local Message Archive opens the account's archive
controls. Native list screens expose the experimental capture switch, bot
exclusion, age and logical-size presets, worker state, dropped-capture count,
deleted-message pages, saved-version pages and confirmed purge. A cloud chat's
avatar long-press menu opens its deleted records; a cloud message's context menu
opens its saved versions. These routes pass message identifiers, not copies of
message text. Version pages contain **captured previous versions**; the latest
live version remains in the normal conversation until it is later captured.
Search, archive media browsing, a full storage dashboard and translated strings
remain outstanding. UI frame/VoiceOver/physical-device review has not run.

Settings are scoped to the account's Postbox. The independently keyed database
lives below `<account basePath>/local-message-archive`, outside the media cache.
The worker is absent from supplementary processes; logout/disposal closes it.
Protected-data notifications stop work and close the connection asynchronously.
Initial access only probes the Keychain; every open requires a fresh
WhenUnlockedThisDeviceOnly key lookup. No cached archive key bypasses that lookup.
The archive has no new network or diagnostic producer.

## Limits that tests must not obscure

- The temporary outbox uses **Postbox's encryption and protection**, not the
  archive's separate key/protection class. Its erased pages are not proven
  forensically overwritten. Account-root backup exclusion is upstream best effort.
- The existing release-mode Postbox commit error path is not a proven durable
  acknowledgement under disk-full/I/O failure. This patch does not change
  authentication, MTProto, Postbox encryption or global commit policy.
- Range/history-validation/local deletions, removal from groups, notification
  extension processing and every other mutation path are not covered by these
  three hooks. Never describe capture as complete or lossless.
- Already-started writes may finish after a settings change or lock transition.
  Purge's generation protocol removes older work; disabling capture pauses future
  capture and does not erase stored history.
- Queue budgeting can scan/re-encode the bounded pending payload on each edit.
  Device latency under a full backlog has not been measured. Logical quotas do
  not cap SQLite/WAL physical allocation. Background execution is not guaranteed.
- A raw malformed Postbox entry may be rejected by upstream decoding before the
  worker sees it. Unknown decoded archive versions are retained and block progress.
- Existing upstream telemetry remains unresolved. A local-only archive worker is
  not proof of a telemetry-free application.

## Verification gate

The new `archive-integration.yml` builds the exact pushed revision on `xcode-27`.
It runs explicit Postbox, LocalMessageArchive, TelegramCore and TextFormat iOS
test targets, publishes `archive-integration-tests-<commit>` before the long
application build, then builds the full simulator app and an unsigned device build. It
publishes bounded redacted test logs, compiler logs, provenance and SHA-256
checksums. Its independent concurrency group never cancels a healthy running
build. Tests run in an independent job for every revision; only full application
jobs are serialized, so a prior long build does not delay current test evidence.
The older baseline run is left untouched. Both jobs use fresh runners and
private credential-bearing Bazel state is cleaned between invocations. Setup
and dependency compilation can repeat. This cost avoids persisting a
secret-bearing build cache.

New TelegramCore tests use the actual on-disk encrypted Postbox and SQLCipher
store. They cover helper capture plus canonical mutation, serialization/reopen,
queue bounds/exclusions, failure without acknowledgement, simulated interruption
after archive commit, account isolation, retention, purge and an injected lock
gate. Seven additional tests call the real `replayFinalState` function with
verification enabled: explicit/global deletion, edit merging, same-batch
receive/edit/delete, disabled capture and stale-state rejection. One connects
replay through the coordinator to real SQLCipher and the read facade. They do
not exercise Telegram servers, actual process kill, real Keychain lock
enforcement, native UI frames or physical installation.
Until the workflow passes, these are **authored tests, not passing evidence**.
