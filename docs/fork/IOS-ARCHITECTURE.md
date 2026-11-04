# Telegram-iOS source audit

Follow-up design amendment: the expanded request requires an **independent
encrypted SQLite archive**, superseding the initial dedicated-Postbox-table
suggestion below. [The implementation contract](IMPLEMENTATION-PLAN.md) describes
the separate SQLCipher database and durable cross-database handoff that must be
designed and crash-tested. This audit's source locations remain relevant.

Audited official repository `https://github.com/TelegramMessenger/Telegram-iOS`, commit `279f89e4d4f63f17227bf877dc3d8990f22ecd58`. Paths and line numbers below refer to this commit. This is source inspection, not a successful build, a dynamic security assessment, or a certification that the upstream client has no telemetry.

## Architecture and invariants

| Layer | Evidence | Constraint for the fork |
|---|---|---|
| Native presentation | `submodules/TelegramUI/Sources/ChatHistoryListNode.swift:517`; `ChatHistoryEntriesForView.swift:36`; `submodules/SettingsUI/Sources/ArchiveSettingsController.swift:146`; `submodules/ItemListUI/Sources/ItemListController.swift` | Reuse UIKit/AsyncDisplayKit, ItemListUI, existing presentation data, localization and accessibility. A WebView or Android screen translation would conflict with the native architecture. |
| Account/use-case API | `submodules/TelegramCore/Sources/Account/Account.swift:1230`; `Sources/TelegramEngine/` | Put account-scoped operations and settings behind the existing account/engine boundaries. Avoid a second session implementation. |
| Update synchronization | `submodules/TelegramCore/Sources/State/AccountStateManager.swift:130`; `AccountStateManagementUtils.swift:4100` (`replayFinalState`) | Preserve update ordering, pts/qts/seq processing, difference recovery, local operation logs and account lifecycle. Do not suppress server updates to retain deleted messages. |
| Local persistence | `submodules/Postbox/Sources/Postbox.swift:3359` (`transaction`), `:1463` (`openPostbox`); `SqliteValueBox.swift:494` | Postbox transactions, serialized queues, indexed history and SQLCipher encryption are the natural persistence boundary. Prefer an account-scoped dedicated archive table/schema, not a parallel unencrypted database or a cache subject to eviction. |
| Reactive concurrency | `submodules/TelegramCore/BUILD:19` (SwiftSignalKit), per-account managed workers | Maintain queue ownership, signal completion/disposal, atomic transactions and worker retry behavior. |
| Protocol and networking | `submodules/TelegramCore/Sources/Network/Network.swift:534,906`; `NetworkEngine.swift`; `MtProtoKitEngine.swift`; `SwitchingNetworkEngine.swift`; generated `submodules/TelegramApi` | Preserve generated TL API, Network request path, MTProto engines, key exchange/session state, retries and transport. Current code can use MtProtoKit and an injected Rust engine; do not assume only one transport. |
| Authentication | `submodules/TelegramCore/Sources/Authorization.swift:188` (`auth.sendCode`), `:1042` (`auth.signIn`); `Sources/TelegramEngine/Auth/` | Use own API ID/hash through NetworkInitializationArguments (`Network.swift:386`); preserve 2FA, existing account migration and push verification. The presence of a Firebase SMS API name is not itself proof of analytics. |
| Cryptography | `submodules/TelegramCore/Sources/SecretChats/SecretChatEncryption.swift:135,262`; dependencies `EncryptionProvider`/`CryptoUtils` in `TelegramCore/BUILD:22` | Do not change cryptographic code or E2EE semantics. Cloud chats and Secret Chats have different security models; ordinary cloud chats are not end-to-end encrypted. |

`AccountManager.swift:287–293` sets the account root's backup-exclusion resource value. Archive storage must live within the intended protected account root, and backup exclusion, lock-state accessibility and logout purge still need device tests; inheritance is not sufficient evidence.

## Feature seams and important limitations

### 1. Local deleted-message archive

Remote deletion is decoded at `AccountStateManagementUtils.swift:1064` (global IDs) and `:968` (channel IDs), with difference-processing channel deletions at `:3563`. Replay applies deletion at `:4624` (`DeleteMessagesWithGlobalIds`) and `:4633` (`DeleteMessages`). Snapshot locally available eligible content **inside the transaction before normal deletion**; let normal history, thread counters and media cleanup finish. Resolve global IDs with `Postbox.Transaction.messageIdsForGlobalIds` (`Postbox.swift:179`) before deletion. Key by account + peer ID + message namespace + message ID, never by bare integer message ID.

The native deletion path removes media cache resources (`AccountStateManagementUtils.swift:4625–4631`; `TelegramEngine/Messages/DeleteMessages.swift:42`). An initial text/metadata-only archive can avoid claiming unavailable media is preserved; retaining media requires a separately reviewed storage/retention design. A message never received or already evicted cannot be reconstructed from a deletion event. Server updates do not reliably identify whether deletion originated from another actor, this account, or another device; label the recorded event as observed deletion rather than inferring the sender.

Do not intercept every `Transaction.deleteMessages` indiscriminately: it is also used by local cleanup, clear-history and other lifecycle work. Expiry, disappearing/view-once messages, Secret Chats, scheduled/quick-reply/ephemeral namespaces and explicit user purge require explicit policy. Recommended first increment: opt-in ordinary cloud text messages only, excluding these cases, with bounded retention and a purge action. Range removal (`UpdateMinAvailableMessage`, `AccountStateManagementUtils.swift:4638`) is another path that needs a documented decision. Device-local deletion must never enqueue uploads, analytics, Saved Messages, CloudKit or backup exports.

### 2. Edited-message version history

Server edit decoding: `AccountStateManagementUtils.swift:989,1072,3566`; replay: `:4670`. `transaction.updateMessage` supplies `previousMessage`, then uses `mergedServerMessageUpdate`; store the prior content before replacement in the same transaction. Preserve that merge so local attributes and reaction handling continue to work. `Postbox.swift:3077` shows the underlying update method and callback behavior.

Not every message mutation is an edit: reactions, polls, delivery flags, media availability and counters also change messages. Compare an explicit content representation and edit identity; avoid storing identical replayed updates, false versions for metadata-only changes, or unbounded attacker-controlled bodies. Deleted-message snapshots may refer to a version, but sharing storage must not blur purge/retention semantics. Versions predating this client's first observation are unavailable.

### 3. Ghost mode

There is no single read/presence switch. Source-level candidate gates include:

- History read synchronization: `State/SynchronizePeerReadState.swift:293,323`; Secret Chat read path at `:275` (`messages.readEncryptedHistory`).
- Discussion/topic reads: `TelegramEngine/Messages/ReplyThreadHistory.swift:490` and `ApplyMaxReadIndexInteractively.swift:222,258,300`.
- Media/content receipts: `State/ManagedSynchronizeConsumeMessageContentsOperations.swift:114,131`; `ManagedConsumePersonalMessagesActions.swift:256,291,325,368`; `ManagedSynchronizeMarkAllUnseenPersonalMessagesOperations.swift:151,160`; `AccountViewTracker.swift:975,979`.
- Secret-chat content-read service actions: `State/ManagedSecretChatOutgoingOperations.swift:162,1641–1649`. Suppressing these changes expiry semantics; keep excluded initially or define and test their behavior explicitly.
- Online announcements: `State/ManagedAccountPresence.swift:41–60`, including its 30-second refresh timer and in-flight request cancellation.
- Typing and upload/record activity: `State/ManagedLocalInputActivities.swift:147–202` (`messages.setTyping`, `messages.setEncryptedTyping`). This also carries unrelated activities such as group-call speaking and emoji interaction, so classify actions instead of blindly dropping all setTyping traffic.
- Additional typing-draft stop path: `State/TypingDraftStop.swift:98`.

Gate emission at the application operation boundary, without replacing encryption/networking. Local unread state and remote synchronization must be separated deliberately. Disabling a receipt must not leave a busy-looping worker or silently flush suppressed receipts after restart/re-enable. Define whether pending receipts are discarded or explicitly released, then test that policy across migration and multi-account state. Suppression cannot undo already-sent events or promise invisibility: another logged-in client and explicit messaging/calling actions can reveal activity.

### 4. Message filtering

Native history mapping begins at `TelegramUI/Sources/ChatHistoryEntriesForView.swift:36`; `ChatHistoryListNode.swift` manages original and filtered entries, paging and visible ranges. A local display predicate should leave server state and archive data intact. Test empty/all-filtered pages, album grouping, replies, search, unread boundaries, notifications, accessibility and scroll anchors; a view-only filter does not automatically filter push notifications or searches.

Existing `TelegramCore/Sources/TelegramEngine/Peers/ChatListFiltering.swift` implements chat-folder filtering and synchronizes dialog filters (`:583–608`); this is **not** a local message-content filter. Do not accidentally send private filtering rules through that API. Prefer bounded literal/type/sender predicates first; unrestricted regex rules need explicit cost limits and pathological-input tests.

### 5. Native settings

`SettingsUI/Sources/ArchiveSettingsController.swift:146–200` demonstrates ItemListController/presentation-data patterns. The archive here is Telegram's chat-folder archive, so use a distinct label for deleted-message storage. Add own per-account preference model and update signals, default archive/history opt-in and suppression disabled, and make destructive purge explicit. Keep inherited localization, dark mode, Dynamic Type and VoiceOver. Do not use synchronized Telegram privacy settings for device-local archive/suppression preferences. Visual verification must follow repository AGENTS.md: simulator recordings and timestamped frames, not unit tests alone.

## No-telemetry requirement: upstream is not compliant as-is

The current official source explicitly includes server-enabled network telemetry:

- `Network/NetworkTelemetry.swift:5–42`: configuration reads `network_telemetry_enabled`, variant and interval.
- `Network/Network.swift:740`: optional RecordingNetworkEngine wrapper.
- `Network/NetworkTelemetryReporter.swift:159–176`: queues app-log events and manages periodic reports.
- `Account/Account.swift:1611`: starts telemetry reports.
- `State/ManagedSynchronizeAppLogEventsOperations.swift:73`: sends `help.saveAppLog`; `State/AccountTaskManager.swift:151` starts this operation worker.
- `Account/Account.swift:1674`: a separate direct pre-auth `help.saveAppLog` path.
- `Network/NetworkStatsContext.swift:90`: download app-log event producer.
- `TelegramEngine/Calls/RateCall.swift:22–26`: `phone.saveCallDebug` logging API; its callers and call-log uploads need separate review.

A no-telemetry fork needs an explicit hard-disable patch with tests covering remote configuration, pre-auth and authenticated paths, queued legacy events and call diagnostics. Merely setting a local default or disabling a UI toggle is insufficient. Preserve authentication/anti-abuse verification and necessary protocol traffic; do not classify every log-like or Firebase-named authorization method as telemetry. No archive payload may reach any diagnostic producer, clipboard export, share flow or upload worker by default. This audit identifies concrete paths but is not an exhaustive proof of absence of telemetry or third-party requests.

## Tests and security acceptance

Existing targets `//submodules/Postbox:PostboxTests` (`Postbox/BUILD:113`) and `//submodules/TelegramCore:TelegramCoreTests` (`TelegramCore/BUILD:64`) use iPhone 17 / iOS 26.5 runners. Invoke explicitly: `Tests/AllTests/BUILD` references `//submodules/TgVoipWebrtc:TgCallsTests`, documented as dangling in CLAUDE.md. Confirm simulator runtime availability separately from Xcode availability. Existing Postbox and core test suites provide integration patterns; a small standalone model test is not proof of integrated app behavior.

Required incremental acceptance includes:

1. Baseline full target build, explicit core/Postbox tests, launch/login smoke test with own credentials, and preserved account state. This Linux source audit has performed none of these iOS tests.
2. Archive: atomic capture/delete, crash recovery, duplicate/gap update replay, ID collisions across accounts/channels, unavailable-message events, bounded retention, expiry exclusions, logout/account removal, protected storage and no backup inclusion. Inspect WAL/temp/media copies as well as the main database.
3. Edit history: sequential/reordered/duplicate updates, idempotent migration, old/current text selection, reaction-only updates excluded, retention and crash rollback.
4. Suppression: request-spy assertions on every identified RPC family, online timers, in-flight changes, restart/reconnect, pending operation behavior, multi-account isolation, notification reads, thread reads and exclusions. Then device traffic observation using test accounts, without archiving production messages in artifacts.
5. Filtering/settings: deterministic predicate tests, bounded input handling, pagination/reply/unread regression, preference migration, UI accessibility and recorded frame review.
6. Security gates: secret scan limited to changed files without printing secret values; pinned dependency/checksum inventory; no new network/import capability in archive module; archive-content canary absent from request bodies, logs and build artifacts; telemetry forced on by hostile server config still emits nothing; source/export manifest; verify independent signing identity and all nested code signatures.

Build pin at this revision: app 13.1.2, Xcode 27.1, macOS 27, Bazel 9.2.0 with SHA-256 `dd466352a3e4d3581b8898740ee1ff208866ccbe25f8d367c5dcb950219587e6` (`versions.json`). A macOS machine, the matching Apple toolchain/runtime, own bundle/team identifiers, own Telegram API credentials and signing material remain necessary. No IPA or successful baseline build is asserted by this document.

## Follow-up: durable archive handoff seam (unimplemented)

Inspected against the same pinned source on 2026-11-04. This is an implementation
handoff, **not a validated integration design or a claim of reliable capture**.

The smallest candidate can reuse Postbox's existing operation log rather than
adding a Postbox table or importing the archive module into Postbox:

- `Postbox/Sources/Postbox.swift:1966–1968` registers operation-log metadata,
  merged index and content tables as 29–31. Public transaction APIs at `:805–850`
  append, enumerate, update and remove entries. A dedicated TelegramCore
  `PostboxCoding` payload and unused operation tag could use those tables without
  a Postbox schema upgrade. Existing tags occupy 0–27 in
  `TelegramCore/Sources/SyncCore/SyncCore_Namespaces.swift:234–263`; reserve and
  collision-check a fork tag explicitly. Register its decoder in
  `Account/AccountManager.swift` alongside the operation types at `:150–229`.
  Version the payload and fail closed on unknown versions; do not serialize an
  entire `Message`/attribute collection containing unrelated state.
- Capture eligible, already-present text/entity snapshots before
  `State/AccountStateManagementUtils.swift:4624` global-ID deletion (`Transaction.
  messageIdsForGlobalIds` resolves IDs first), `:4633` explicit deletion, and
  inside the `previousMessage` closure at `:4672` before returning the merged edit.
  Append the snapshot and canonical mutation in the **same** Postbox transaction.
  Preserve a stable event UUID, original observation time and per-account purge
  generation in the payload. Compare actual content so reaction/counter changes
  do not create fake edit versions. Keep secret/TTL/view-once content excluded.
  This limited hook set does not cover range deletion at `:4643`, history
  validation, local removals, account removal or every expiry path.
- Use `Postbox.mergedOperationLogView` only as a wakeup. Its initial value emits
  inside `transactionSignal` (`Postbox.swift:4152`), and later view updates occur
  in `beforeCommit` (`:2778`), **before** `valueBox.commit` (`:3311`). Hop to a
  separate serial account coordinator, re-read a bounded set through a fresh
  `postbox.transaction`, and use that transaction's emitted result (`:3375`) to
  start asynchronous archive I/O. Never write the archive directly from a view
  callback or transaction closure. Keep the entry queued until archive success;
  then acknowledge that exact event in another Postbox transaction. Restart after
  archive commit but before acknowledgement must replay the same event key.

There is an unresolved durability caveat: `SqliteValueBox.swift:681` calls
`checkTransactionResult` (`:26`), which logs and asserts on commit failure. The
stronger global error mode is documented for the notification extension, not the
main app. A normal transaction signal is therefore **not established as a durable
commit acknowledgement under release-build disk-full/I/O failure**. Do not enable
that global mode in the main app as a shortcut. Commit-failure behavior needs a
scoped review and fault-injection tests before a reliability claim.

Purge also needs a persistent protocol, not just `ArchiveStore.purge()`: pause the
account drain, persist a new capture generation/pending-purge marker, atomically
advance the archive's accepted generation and delete prior records, then retire
old outbox entries and resume. The archive must reject in-flight old-generation
writes across restart. The schema-3 library draft now adds a local generation
barrier verified by native library tests at `c326364b`; the Postbox coordinator
and application-level purge tests are still absent. Bound outbox
bytes and report capture gaps when storage is unavailable; never silently claim
lossless capture while allowing unbounded backlog. Existing operation-log removal
uses `secure: false` (`PeerOperationLogTable.swift:195`), so it is not forensic
payload erasure. Account-root backup exclusion is attempted at
`AccountManager.swift:287`, but its failure is only logged; this does not prove
outbox backup/protection guarantees or make it use the archive's independent key.

Minimum tests should combine the real transaction/view APIs with an injected
archive sink: no consumption of pre-commit view data, close/reopen persistence,
archive failure without acknowledgement, replay after success-before-ack, purge
with an in-flight event, exclusions, and unchanged canonical deletion/edit results.
`Postbox/Tests/PostboxFixture.swift` is an in-memory starting point, so add a
persistent fixture for reopen/crash tests; it cannot prove crash durability as-is.
Explicit targets already exist at `//submodules/Postbox:PostboxTests` and
`//submodules/TelegramCore:TelegramCoreTests` (both iPhone 17/iOS 26.5). Actual
commit-failure/crash tests, media ownership and coverage of all removal paths
remain required before connecting the draft to production message processing.
