# AyuSync compatibility and security audit

Audit date: 2026-11-04. Read-only source/protocol investigation; no sync client was implemented, no application was built or launched, and no live AyuSync service was contacted. No archives, read states, authentication material, or device identifiers were uploaded. GitHub repository metadata and public Git references were read. This protocol review is not a feature completion claim.

## Decision

**Do not enable or implement compatibility with the inspected legacy AyuSync protocol in this increment.** The available Android client sends readable JSON to its service and contains no end-to-end encryption or client key lifecycle. Its edited-history upload loop is commented out, and no active deleted/archive/media replication protocol is established. The advertised backend source is unavailable to this audit. Desktop's similarly named `AyuSync` namespace is a different facility for synchronous Telegram forwarding.

The iOS archive remains local-only with no AyuSync transport, registration, account token, remote endpoint, or upload UI added. Its existing archive encryption key must stay local; Telegram login codes, authorization keys, session databases, access hashes, and other session material must never be used as a sync credential or sent to a sync service. Future synchronization needs separately reviewed, explicitly enabled, per-account consent and an interoperable encrypted protocol; neither exists in the evidence reviewed here.

## Exact sources and availability

| Source | Revision / evidence | Scope |
| --- | --- | --- |
| Android | [`7013145676d36d82ee13c02a89f72097b7490dcd`](https://github.com/AyuGram/AyuGram4A/commit/7013145676d36d82ee13c02a89f72097b7490dcd), committed 2023-07-28T12:16:39Z | Historical public client, not October 2026 Android source. Root working tree was clean. |
| Desktop | [`0f2a31bb420de98f331990432265b131b8d836b5`](https://github.com/AyuGram/AyuGramDesktop/commit/0f2a31bb420de98f331990432265b131b8d836b5), committed 2026-10-20T22:08:03+03:00 | Newer C++/Qt source, no confirmed cloud AyuSync implementation. Root working tree was clean. |
| Documentation | [`42f5f32c6b05aab4a69a5f8d0bf7652e1cb11a9b`](https://github.com/AyuGram/AyuGramDocs/commit/42f5f32c6b05aab4a69a5f8d0bf7652e1cb11a9b), dated 2025-01-08 | `android/saving.md:15` says messages can be synchronized, but provides no protocol, encryption or key-management specification. |
| iOS fork | [`f295948ad9ba8fb1cb668dc0b645c486d1f2ec74`](https://github.com/Livara-Dev/Telegram-iOS/commit/f295948ad9ba8fb1cb668dc0b645c486d1f2ec74) | Current local archive implementation used for schema comparison. |
| Advertised backend | Android `README.md:78–84` links `https://github.com/AyuGram/AyuSyncBackend` | `git ls-remote ... HEAD` returned “Repository not found”; the connected GitHub `get_repo` returned HTTP 404 on 2026-11-04. No backend commit or license could be obtained. This does not establish whether it was deleted, renamed, or made private. |

Connected GitHub repository search for `AyuSync` scoped to organization `AyuGram` returned no repositories. A broader name search returned unrelated or unverified third-party repositories; none was substituted for the official backend. No authentication/token-issuance endpoint was exercised. Backend storage, account authorization, deletion behavior, TLS deployment, quotas, and service availability therefore remain unverified.

Existing evidence and related reviews: [source evidence manifest](evidence/evidence-manifest.json), [saving-page snapshot](evidence/saving.html.gz), [release metadata](evidence/releases.json), [Android audit](AYUGRAM-REFERENCE.md), [Desktop audit](DESKTOP-REFERENCE.md), [October release audit](EXPANDED-REFERENCE.md), and [license review](LICENSE-AND-SUPPLY-CHAIN.md). `saving.html` SHA-256 is `2ff452e96ad9efa8b4cebbaaaa20909f6ba0130f49ea8a8ecc4b7046f197dfcc`. October release notes identify Android build **20261005**, based on exteraGram **12.10.6**, and mention database import/export improvements; they do not specify encrypted sync or supply its implementation. No October Android source or current-service compatibility is claimed.

All Android paths below are relative to `TMessagesProj/src/main/java/com/radolyn/ayugram/` unless prefixed otherwise. Desktop paths are relative to `Telegram/SourceFiles/`.

## Legacy Android protocol actually present

`sync/AyuSyncConfig.java:14–43` constructs endpoints from the configured server. `AyuConstants.java:48` defaults to `ayusync.cloud`, or `ayusync-dev.radolyn.com:5000` for beta. `AyuConfig.java:69,112–116,184–189` stores one application-wide configuration in Android private SharedPreferences: sync defaults off; secure transport defaults on for non-beta and off for beta. The UI can toggle secure transport and change the server/token (`ui/preferences/AyuSyncPreferencesActivity.java:119–176`). These are source defaults, not confirmation that either server currently operates.

| Operation | Request / observed fields | Source |
| --- | --- | --- |
| Read service profile | `GET /user/v1`; `Authorization` contains the configured service token, and `X-APP-PACKAGE` contains the Android package name | `sync/AyuInterceptor.java:20–30`; `AyuSyncController.java:178–200` |
| Register device | `POST /sync/register/v1`, JSON `name`, `identifier` | `AyuSyncController.java:137–158` |
| Request full synchronization | `POST /sync/force/v1`, JSON `userId` for the selected Telegram account, `fromDate: 0` | `AyuSyncController.java:203–230` |
| Stream events | `/sync/ws/v1`; headers `Authorization`, `X-APP-PACKAGE`, `X-DEVICE-IDENTIFIER`; textual JSON frames | `sync/AyuSyncWebSocketClient.java:31–55,83–115` |
| Open profile in browser | `/ui/profile?token=<configured service token>` | `AyuSyncConfig.java:42–43`; preferences controller `:164–165` |

The transport is `https`/`wss` when secure mode is set and `http`/`ws` otherwise. TLS can protect transport to the service; it does not prevent that service from reading the serialized fields. No application-level ciphertext envelope is applied before `super.send(message)`. No custom certificate pinning or certificate policy is configured in the inspected wrapper; the actual TLS behavior depends on the external HTTP/WebSocket libraries and was not runtime-tested.

Device registration sends `Build.MANUFACTURER + " " + Build.MODEL` and `Settings.Secure.ANDROID_ID` (`AyuUtils.java:184–190`). The WebSocket repeats the identifier. The client loads **all activated Telegram accounts** into a `Telegram user ID → local account slot` map (`AyuSyncController.java:98–106`); this is not independent opt-in per Telegram account.

The profile model is `id`, `accessToken`, `isMVP` (`sync/models/AyuUser.java:12–18`). `connect()` requires a nonempty configured token, fetches the profile and checks `isMVP` before registration (`AyuSyncController.java:109–134`). This is service-token authentication, not a reviewed Telegram authorization exchange. Token issuance, cryptographic validation, expiry, rotation, revocation, and proof that a client controls a claimed `userId` cannot be established without the backend. Nothing in these client paths justifies uploading Telegram session material.

Active WebSocket messages:

- `sync_read`: `userId`, then `args.dialogId`, `args.untilId`, `args.unread` (`sync/models/SyncRead.java:12–23`). The values are serialized directly with Gson and sent (`AyuSyncController.java:288–306`). A ghost-mode intercepted read request can trigger it (`TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java:327–355`). Thus hiding a read from Telegram does not hide this read metadata from the configured AyuSync service.
- `sync_batch`: `userId`, `args.events`, a list of nested events (`sync/models/SyncBatch.java:14–21`). A force-sync response enumerates local dialogs and sends a batch of read positions/unread counts (`AyuSyncController.java:234–264`).
- `sync_force`: `userId`, `args.fromDate` (`sync/models/SyncForce.java:12–19`), handled by `onForceSync`.
- `sync_force_finish`: model has `type`, `userId`, `args`; the sender constructs it without assigning account/args (`AyuSyncController.java:278–279`, `sync/models/SyncForceFinish.java:12–20`). This is not evidence of a durable, per-event acknowledgment protocol.

The inbound dispatcher only recognizes force, batch and read (`AyuSyncController.java:322–348`). A received read can alter **local** dialog read state using `AyuGhostUtils.markReadLocally` (`AyuSyncController.java:309–319`; `utils/AyuGhostUtils.java:48–60`). It is not an archive importer. `accountExists` also allows `userId == 0` (`:351–352`); source presence alone does not demonstrate correct authorization for all message types.

## Encryption, secret handling and delivery gaps

There is no client E2E key generation, device pairing, recipient key authentication, key rotation, AEAD envelope, nonce handling, or replay ledger in the inspected `sync/` implementation. Searches for the corresponding encryption/security primitives in that directory and its settings controller returned no matches, and the actual send/receive paths operate on JSON directly. Consequently no encrypted cross-device archive protocol or key lifecycle can be derived from this client. Backend-at-rest encryption, if present elsewhere, would not make these plaintext-to-service messages E2E encrypted.

The service token is obtained with `SharedPreferences.getString`, not a dedicated keystore operation (`AyuConfig.java:188–189`), and the settings row displays it (`AyuSyncPreferencesActivity.java:207`). The profile URL puts it in a query string; browser history, URL logs and referrers depend on the deployment and were not tested. Do not port this token-in-URL pattern. The client logs full outgoing read JSON and incoming event JSON (`AyuSyncController.java:305,323`), exposing account/dialog/read metadata to logs. Do not reproduce those logs in iOS.

`AyuSyncWebSocketClient.java:47–52` sets connect/read timeouts and automatic reconnection; `send` catches exceptions and updates a last-sent timestamp after `super.send` (`:83–91`). That timestamp is not a server commit acknowledgment. No persistent outbound event queue, event ID, delivery deduplication, monotonic cursor, or crash-recovery transaction is visible in these paths. `onBatchSync` recursively dispatches events without an explicit count/depth limit (`AyuSyncController.java:282–285`); Gson receive handling has no application-defined frame/decode bound (`AyuSyncWebSocketClient.java:102–109`). Library-imposed limits and backend limits remain unverified. These are compatibility/reliability risks, not demonstrated exploitation results.

## Archive compatibility

The Android README says history can sync, but the only edited-history sender at `AyuSyncController.java:266–276` is commented out. `database/dao/EditedMessageDao.java:33–37` supplies count and 50-row paging queries, yet unused queries are not a working protocol. `SyncMessageEntity.java` contains only `byte[] data` and is not referenced by the active dispatcher. No active archive delete/edit/media importer or attachment-upload route was established.

| Concern | Android evidence | iOS archive at `f295948…` | Interoperability consequence |
| --- | --- | --- | --- |
| Account identity | Sync `userId` is `UserConfig.getClientUserId()`; archive `AyuMessageBase.userId` | `Account.swift:1653–1659` gives `AccountRecordId.int64` to the archive coordinator | IDs are different namespaces. A trusted explicit mapping is needed; copying numbers could merge or leak accounts. |
| Peer/message identity | `dialogId`, `peerId`, `messageId`, optional topic/group fields | `ArchiveMessageID` includes account, encoded Postbox peer, message namespace and message ID | Need explicit user/group/channel/topic/namespace conversion. Signed Android dialog IDs must not be assumed identical to Postbox peer encoding. |
| Stored content | `database/entities/AyuMessageBase.java:12–49`: text, TL-serialized entities/media attributes, reply/forward fields, absolute local media paths | `ArchiveModels.swift:33–58`: text, author, sent time, opaque versioned entity data; adapter `LocalArchiveCapture.swift:6–56` uses JSON entities | Neither payload nor serialization is drop-in compatible. Current iOS archive does not retain media; absolute Android paths are not portable attachments. |
| Version identity | Local auto-increment `fakeId`; `entityCreateDate`, `editDate`; paging by time | Stable captured event key, event kind, observed time, previous version's server edit time, sequence | Need explicit event identity, timestamp semantics and duplicate/conflict rules across devices; local sequence/fake IDs cannot provide them. |
| Crash recovery / retention | No active archive-delivery acknowledgment demonstrated | `ArchiveStore.swift:106–138` persists local outbox high-water receipt; `:206–225` generation/purge barrier | Receipt describes a single ordered local Postbox outbox, not multiple remote devices. Never feed remote events into it as if they were local indices, or reset it during sync. |
| Local encryption | Independent Room schema; no E2E archive format established | SQLCipher archive, schema 4, authenticated open, per-account metadata check (`ArchiveStore.swift:230–288`) | Encrypted SQLite files still are not a safe conflict-aware synchronization format. Copying active DB/WAL files can lose writes and cannot solve key transfer. |
| Keys | No sync content-key lifecycle visible | 32-byte random local key; `WhenUnlockedThisDeviceOnly`, `Synchronizable=false`; failure does not regenerate an existing archive key (`KeychainArchiveKeyStore.swift:26–99`) | Current key intentionally does not migrate. Never weaken its protection or export it to make legacy sync appear compatible; any future export/sync envelope needs a separately reviewed key lifecycle. |

iOS file paths in this table are under `submodules/LocalMessageArchive/Sources/LocalMessageArchive/` for `Archive*.swift` and `KeychainArchiveKeyStore.swift`, or `submodules/TelegramCore/Sources/` for `Account/Account.swift` and `LocalArchive/LocalArchiveCapture.swift`. Local retention/purge and account-isolation tests do not verify multi-device synchronization. Remote replay must never resurrect records removed by local retention or explicit purge.

## Desktop distinction and licenses

Desktop `ayu/features/forward/ayu_sync.h:25–88` declares `loadDocuments`, `sendMessageSync`, `sendDocumentSync`, `uploadFileSync` and `sendRichMessageSync`. The implementations call the existing Telegram sender/uploader and generated MTProto requests (`ayu_sync.cpp:500–558,650–751,846–914`); `ayu_forward.cpp:369,437` calls them during forwarding. This namespace means synchronous helpers in this path, not the Android REST/WebSocket service. Searching the inspected Ayu feature subtree found no `ayusync.cloud` endpoint or legacy cloud read-event dispatcher. Existing `docs/fork/DESKTOP-REFERENCE.md` reaches the same distinction. This is not a proof that every external Desktop component is free of all other network behavior.

Android root `LICENSE` is GPLv2; Ayu-authored headers request original-author credit. Its `.gitmodules` points at a separate uninspected component, which was not used as a source of sync implementation. Desktop root `LICENSE` is GPLv3; `LEGAL:6–17` states GPLv3-or-later and an OpenSSL linking exception. These different licenses must not be conflated. The unavailable backend's license and corresponding source cannot be verified or inferred from the Android license. No code or dependency was copied in this audit. The old Android dependencies (`TMessagesProj/build.gradle:67–69`, WebSocket client 2.0.2 and OkHttp 4.10.0) were not introduced into iOS, audited for vulnerabilities, or executed.

Any future distributed implementation must preserve applicable notices, verify license compatibility for copied expression and dependencies, and supply complete corresponding source/build inputs for its exact binary. Protocol investigation does not waive those obligations or resolve Telegram API-policy conflicts concerning read/activity suppression.

## Required evidence before reconsidering implementation

1. Obtain the actual supported server/client specification and source revision, license, current version negotiation, authentication/authorization rules and account ownership mechanism. A 2023 client or 2026 import/export release note is insufficient.
2. Establish an independently reviewed encrypted content protocol with authenticated device pairing, per-account key separation, compromise/revocation/recovery behavior and bounded parsing. Use existing vetted platform primitives; do not invent a custom cryptosystem or treat TLS alone as E2E encryption. Define exactly which metadata the service necessarily learns.
3. Define a portable archive schema and versioned conversion of identity, entities, timestamps and supported attachments. Separate remote event identities from local outbox receipts; enforce purge/retention/exclusion semantics and account isolation across replay and reconnect.
4. Require an explicit per-account user action, endpoint/recipient disclosure, local-only default, reversible disable behavior and a preview of data leaving the device. Consent to archives must not authorize contacts, all accounts, logs or Telegram session material. If no compatible E2E mode exists, keep synchronization unsupported rather than silently downgrade.
5. Verify with local mock transports and temporary synthetic archives: wrong-account rejection, corruption/tamper/nonce/replay tests, duplicate delivery, out-of-order edits, key loss/revocation, purge-versus-offline-device races, partial media, storage pressure, process termination, cancellation and zero network activity while disabled. A later authorized live test needs recorded wire-level evidence and must never log secrets or content.

No production synchronization code, network writes, live protocol tests, encryption interoperability results, or successful cross-device synchronization are part of this audit.
