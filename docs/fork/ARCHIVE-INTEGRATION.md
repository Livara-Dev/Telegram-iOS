# Experimental application archive integration

This increment connects the native storage library to TelegramCore and SettingsUI.
It is **default off and not yet verified in the running Telegram application**.
Commit `1b7dfc3b` passed 54 macOS library tests and an iOS module compile in
[run 41811248751](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41811248751).
Those results cover the standalone schema-4 store, not the new application hooks
or native screens. Every subsequent source revision needs its own CI result.

## Capture and delivery

`AccountStateManagementUtils.replayFinalState` calls the capture adapter before
global-ID deletion, explicit server deletion, and replacement of an edited
message. It only copies an already-present ordinary cloud message's text,
versioned text entities, identity and timestamps. Secret Chats, expiring and
view-once messages, expired/service content and copy-protected content are
excluded. Unknown conversation peers are excluded. Media files are not copied;
media-only edits are not text versions. Pure reaction/count/edit-date changes
do not create a version.

The user-initiated message-delete helper also captures after expanding message
groups but before canonical removal. Its owner comes from the same Postbox's
authorized account state; missing authorization never guesses an archive owner.
The four edit-RPC acknowledgement branches use a common replacement helper that
captures before the old text disappears. The existing incoming flag, live-location
tag and unlocked-media merge rules remain unchanged. Subsequent server replay
sees the deletion/replacement and must not create a duplicate version. These hooks
do not change or suppress outgoing edit/delete requests.

Structured `RichTextMessageAttribute` content is not supported by this text-only
archive. Telegram can keep that content outside the flat message text, so saving
the empty flat field would misrepresent successful capture. Eligible rich-content
deletions and changed rich prior versions are skipped and increment the visible
missed-capture count. Unchanged rich content and deliberate policy exclusions do
not increment it. A normal-text to rich-content edit can still preserve its normal
previous version. Sender exclusions also fail closed when the author is unavailable.
The counter reports missed capture attempts, not a count of unique lost messages.
Changes to metadata inside an unsupported rich payload can also increment it;
the client-only cached full page is excluded from that comparison.

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
- Range/history-clear and history-validation deletions, validated history
  replacements, removal from groups, notification extension processing and other
  mutation paths remain uncovered. The named user-initiated message-delete path
  is not coverage of every local deletion. Never describe capture as complete or
  lossless.
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

The follow-up suite contains 52 archive integration tests: 25 capture/policy,
14 local-mutation/replay, five acknowledged-edit merge and eight coordinator
tests. Added regressions cover user deletion followed by duplicate server replay,
acknowledged own edits, group expansion, rich-content gaps and unknown-author
exclusions. The cache-removal test uses the actual `MediaBox.removeCachedResources`
endpoint with the production directory layout, verifies that cached bytes were
removed, then reopens both databases and checks archived/canonical records. It
does not test the Settings UI's cache-selection flow. These additional tests
also require a fresh native result; they do not inherit the earlier commit's result.

## First runtime failures and corrections

At `b6d6e634`, [run 41809250180](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41809250180)
passed the real Postbox and LocalMessageArchive iOS test targets and compiled
TelegramCore. Its Core test process then found two failures. The first archive
test trapped because `LocalArchiveSettings.schemaVersion` used Swift `Int`,
which Postbox's adapted encoder deliberately rejects. The field now uses
`Int32`, with explicit fixed-width preference serialization and a byte-level
PreferencesEntry/Postbox reopen regression. The Postbox codec itself is unchanged.

The upstream rich-quote round-trip fixture also expected derived display text
while constructing a quote with the storage initializer and an empty display
string. It now uses the existing production rich-quote initializer. The original
assertions remain, with added pre/post serialization entity checks. No quote
production behavior was changed to make the test pass. Both corrections require
a new native CI result; the failed run is not a successful integration claim.
