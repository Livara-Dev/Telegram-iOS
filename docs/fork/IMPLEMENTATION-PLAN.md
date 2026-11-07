# Incremental implementation and evidence gates

This is the implementation contract after the expanded 2026-11-04 request.
Nothing below is marked implemented. Keep independent feature commits; never
replace the official authentication/session/MTProto stack or translate Java.

## A — Establish the baseline

Use the existing checkout and hosted `xcode-27` workflow. Preserve upstream Xcode
27.1/Bazel 9.2.0 pins, initialize exact submodules, install the existing tests'
iOS 26.5 runtime, build the full application and execute explicit test targets.
Retain actual compiler/test output and artifact checksums. A simulator artifact
is not an iPhone artifact, and an unsigned device IPA is not installable as-is.
Do not start integrating message behavior until this baseline is reproducible.
Remove optional upstream telemetry in a separate reviewed diff before exercising
real accounts; require hostile-server-config and queued-report regression tests.

## B — Independent encrypted archive and edit history (highest priority)

Use the already vendored SQLCipher with a separate database per account, outside
Telegram's evictable media cache. Do not use a parallel authentication system or
an Android database dependency. Generate an independent random archive key using
Security.framework; persist it as a non-synchronizing, this-device-only Keychain
item. Use a documented Data Protection class for DB/WAL/SHM/media. Treat locked
device unavailability as a recoverable state, never as a reason for plaintext
fallback. Exclude all archive files from backup and Spotlight. Resolve logout,
account deletion, key loss and explicit purge semantics before shipping.

Two databases cannot be made atomic by wrapping each in its own transaction.
An encrypted Postbox outbox written in the same transaction as canonical deletion
or overwrite can provide durable handoff. Its contents require the same exclusions
and retention protections. An idempotent worker commits the independent archive,
then acknowledges the outbox. Test crashes before/after every step; never falsely
acknowledge a failed archive write. Do not block Telegram's update transaction on
media copying or UI work. Disk-full behavior must be visible and must not corrupt
canonical state. Design the outbox schema and migration with Postbox's maintainers'
invariants before adding a new table/key.

Use account/peer/namespace/message identity, local monotonic revision sequence,
server edit time when provided, and a separately labeled local deletion-observation
time. Deletion updates do not provide reliable actor or historical deletion time.
Capture content before merge/overwrite, compare semantic content, and deduplicate
replayed updates. Preserve original ordering metadata and typed media descriptors.
Unknown/uncached content is unavailable, not recoverable.

For media, copy only eligible accessible bytes into archive-owned protected files
with transactional staging/rename, bounded streaming and explicit pending/missing/
complete states. Reference counting and periodic reconciliation must handle shared
files, quota eviction, interrupted downloads and orphan cleanup. A SQLCipher DB
does not encrypt separate media files; use an authenticated, reviewed encrypted
file format with independent nonces/keys if archive-layer media encryption is
required in addition to iOS Data Protection. No bespoke cipher construction.

Initially exclude Secret Chats, TTL/view-once/disappearing content, unsupported
namespaces, opted-out peers/senders and bots according to settings. Support size
and age limits, indexed paging, asynchronous bounded queries and per-account
quotas. Clearing Telegram's normal cache must leave archive-owned files intact.
Deletion of an archive record must not resurrect it via retries or import.

Expose a deleted-message browser, indexed local search, a distinctly labeled
archive media view, chronological edit timeline, storage dashboard and configurable
markers. Do not pass archive records to normal send/forward/upload paths. Integrate
UI via TelegramEngine and native ItemListUI/presentation data. Add settings with
each feature so there is no inaccessible hidden retention behavior.

Encrypted import/export comes after storage correctness: versioned manifest,
authenticated encryption, bounded archive expansion and path validation, schema
validation, rollback on wrong key/truncated payload, account ownership checks and
an explicit user-selected destination. Never export Telegram session material.

Required tests: content-before-delete/overwrite, account/namespace collisions,
duplicate/gap/out-of-order replay, reactions versus edits, crash matrix, migrations,
key/lock state, cache-clear independence, interrupted media copy, quota/retention,
SQL parameterization, corrupt DB/imports, no plaintext or archive canary in logs,
requests, backups, clipboard or other accounts. Run actual integration tests on
iOS as well as unit tests; a standalone storage test is not the feature.

## C — Account-scoped privacy controls

First resolve the documented Telegram API-terms conflict. Any experimental
implementation must show individual active protections and limitations, default
off and remain independent per account. Enumerate message/content/discussion/story
receipt paths, all classified input activities, presence timers and alternate
notification/extension paths. Do not synthesize successful RPC responses or alter
transport/encryption. Define pending receipts, re-enable behavior and in-flight
races; avoid retry loops. Returning offline is a best-effort request after activity,
not proof that no observer saw the online state. Sending, calls and another logged-in
client can expose activity. Keep scheduled sending separate until delivery
semantics/reliability are proven. Add story warning and quick toggle only with
honest per-protection state.

Required tests use a request recorder and two owned test accounts: read/content/
story RPCs, typing/sticker/record/upload actions, send/read-on-interaction,
reconnect/background/termination, toggle while queued/in flight, account switching,
and simultaneous unmodified client. Verify remote-visible behavior as well as
absence of local requests. Never label the result perfect invisibility.

## D — Local filtering and customization

Global rules plus account/chat exceptions, literal/case-sensitive matching, media
and blocked-sender predicates are the first increment. Store rules locally, not
in Telegram's synchronized folder APIs. Then add a safe regex subset/engine with
documented cost bounds; truncating input alone does not bound catastrophic
backtracking. Cache by account, message version and rule generation; test invalid
patterns and adversarial strings off the main thread. Include paging, albums,
unread anchors, previews, notifications, search, rule-from-message and temporary
reveal. Canonical server messages remain untouched.

Native settings inherit existing themes/accessibility/localization. Local folder
colors/tags must not mutate server entitlements; cosmetic Premium-like appearance
must not unlock paid APIs or imply an entitlement. Test settings search, restore,
multi-account persistence, Dynamic Type, VoiceOver and recorded animation frames.

## E — Optional advanced capabilities and sync

Retain only already accessible, eligible history after group removal; do not
promise backfill or bypass protected media controls. Evaluate copy-protection
flags before enabling any forwarding/export affordance. Preserve expiry rules.

AyuSync is a protocol investigation, not authorization to upload archives. Verify
its complete current protocol, end-to-end encryption, authentication, key recovery,
replay protection and deletion/tombstone handling. Implement only if compatible
with the user's no-developer-service/no-session-upload constraints; explicit opt-in
must select a reviewed user-controlled destination. Default stays local-only.
Never upload authentication codes, session keys, tokens or Telegram account files.

## F — Release evidence

Run regression suites and canary network tests, verify dependency licenses/source,
and measure iPhone 15 Pro cold startup, scroll frame pacing, peak RSS, database
latency under large histories, and battery/background work. Record dataset size,
OS/device, repetitions, baseline and p50/p95 rather than inventing target results.
Verify every nested code signature/profile/entitlement against the actual signing
identity, install on a non-jailbroken iPhone, and pair the SHA-256 IPA with exact
corresponding source/build instructions. Each phase gets a separate commit and
feature-matrix evidence update; a compile alone never marks functionality complete.
