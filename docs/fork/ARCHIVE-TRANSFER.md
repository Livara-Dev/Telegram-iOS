# Encrypted archive backup and restore

The native **Storage & Backup → Encrypted Archive Backup** screen calls the account's `LocalArchiveCoordinator`, then the existing `ArchiveStore`. This is a manual backup of retained archive history, including captured media. It does not export Telegram's live Postbox, authorization/session databases, Keychain credential stores, capture preferences, operation-log receipts, or the persistent archive encryption key. An archived message or document can itself contain a password or other sensitive information; its contents are included like other retained archive content.

Implementation and native tests are present. Native compilation, test execution, document-provider behavior, Data Protection behavior, and visual verification must be qualified by the macOS workflow before being reported as working. The cryptographic implementation has received an internal source review; it has not received an independent external cryptographic audit. It is not AyuSync-compatible and adds no networking or synchronization.

## User flow and ownership

1. Unlock the device, open the account's backup screen, and pause automatic capture. The foreground screen, including its explicitly opened picker/password/transfer subflow, holds an archive read lease; opening it does not enable capture. Backgrounding releases access; foregrounding reacquires it even when a full-screen picker covers the presenting view. Leaving the screen outside that subflow releases the lease.
2. For export, choose a password of at least 12 characters, repeat it, and create the encrypted file. Use a unique strong passphrase; there is no password recovery. The system document picker asks for the destination. No file provider receives a file until this explicit action. A cloud provider chosen by the user receives ciphertext.
3. For import, acknowledge the history-restore warning, choose one encrypted file with the system picker, and enter its password. Password, ownership, payload digest, schema, integrity and bounded row validation finish before the coordinator may initialize a new persistent destination archive or request its new Keychain key.
4. Restore merges immutable archived versions and media. Identical records are deduplicated; an event-key or media-content conflict aborts the entire merge. Restore does not change live Telegram messages. Current retention and logical storage limits apply inside the destination transaction.

The pause button requires confirmation that **new edit/delete events while paused are not captured**. This affects long exports as well as imports. No transfer automatically pauses or resumes capture, and capture stays paused until the user explicitly resumes it. The coordinator currently rejects transfers while enabled; allowing export while continuing to enqueue new Postbox captures is a future optimization, not implemented behavior. Existing queued captures are not exported until they have actually reached the archive. This limitation must be considered before choosing to pause.

Ownership uses the authenticated Telegram user PeerId and production/test environment, not only the installation's local account-record identifier. A backup from another installation of the **same Telegram user in the same environment** can be merged; only the archival `account_id` namespace is mapped. Peer/message identifiers and event keys retain their original values. A different Telegram user, or an equal numeric peer ID in the other environment, is rejected.

Manual restore explicitly permits reintroducing history previously purged or currently excluded from automatic capture. The native confirmation discloses this. Capture settings, purge generation and acknowledged-outbox high-water marks are never imported. A destination with missing archive storage and a nonzero recorded purge generation currently fails closed rather than repairing the generation automatically. A damaged existing archive or missing existing key is never silently overwritten or rekeyed.

## Format version 1

All integer fields in the binary framing/authenticated data are unsigned big-endian. Signed identifiers use their 64-bit two's-complement bit patterns.

| File component | Encoding / bound |
| --- | --- |
| Magic | Eight ASCII bytes `AYUARC01` |
| Header length | UInt32, 1…4096 bytes |
| Header | UTF-8 JSON, exactly the six fields below |
| Payload | Standalone encrypted SQLCipher database, 4096 bytes…8 GiB |

| Header field | Encoding / value |
| --- | --- |
| `version` | UInt32, exactly `1` |
| `iterations` | UInt32, exactly `600000` |
| `salt` | 16 random bytes, JSON base64 |
| `payloadBytes` | UInt64, exact payload size |
| `payloadDigest` | SHA-256 of encrypted database bytes, 32 bytes, JSON base64 |
| `wrappedKey` | AES-GCM combined representation, 77 bytes, JSON base64 |

The password is UTF-8, at most 1024 bytes. Apple CommonCrypto PBKDF2-HMAC-SHA256 with the random salt and 600,000 iterations derives a 256-bit key. Unknown KDF work factors are rejected before derivation. Apple CryptoKit AES-GCM wraps the following 49 bytes: fresh transport key (32), source local account-record ID (8), stable Telegram owner ID (8), and environment (`0` production, `1` test). CryptoKit supplies the random 12-byte nonce; the combined value also includes the 16-byte authentication tag.

AES-GCM additional authenticated data is `magic || version:4 || iterations:4 || salt:16 || payloadBytes:8 || payloadDigest:32`. Semantic header values are authenticated; JSON whitespace/key order is not part of the cryptographic contract. The encrypted payload digest is checked in full before SQLCipher opens it. A changed/truncated file, wrong password, wrong owner/environment, unsupported format/schema, extra schema object, malformed row or failed structural check is rejected. SQLCipher's existing authenticated-page protection also applies. Format version 1 accepts the current archive schema version 8 only; future versions must add explicit compatibility/migration rules rather than execute imported SQL.

The account's persistent Keychain key is never placed in the export. Each export uses a fresh random 256-bit SQLCipher transport key and newly encrypted database. Raw keys exist in process memory during their necessary operations; the implementation does not promise complete erasure of Swift/Framework memory copies. Password-derived temporary byte buffers are cleared where supported. No plaintext database or media file is created by this transfer path.

## Snapshot, merge and resource bounds

Export first validates the source schema and applies its current retention policy. It then holds a source SQLite WAL read transaction and copies only retained event/media rows, using fixed prepared SQL, into a newly encrypted current-schema database. Incremental media BLOB reads/writes use 512 KiB buffers. Destination triggers rebuild search/gallery indexes from those rows. A whole-file SQLite backup is deliberately avoided: obsolete FTS shadow segments and freelist content can retain text from purged messages even when normal queries no longer find it. This reconstruction prevents those obsolete segments becoming part of a password-authorized export. It is not a claim of forensic secure erasure on flash storage.

Import uses a read-only, `SQLITE_OPEN_NOFOLLOW` encrypted staging database with bounded SQLite limits and `trusted_schema=OFF`. It compares schema objects to a freshly generated encrypted current-schema reference and validates field types, sizes, account, event kinds, timestamps, media slots/costs and orphan relationships before allowing destination initialization. It never runs imported DDL or triggers on the destination. Merge and retention share one destination transaction with generation checks; interruption or a conflict rolls uncommitted changes back. Destination schema and capture state remain destination-owned.

Current hard transfer limits are two million message versions, 256 KiB UTF-8 message text per version, 64 KiB entity metadata, ten media slots per version, 512 MiB per media BLOB, and an 8 GiB encrypted database payload. The current account storage limit is independently at most 1 GiB of logical payload, which is not a bound on SQLite/WAL physical allocation. Large exports require additional free space for the encrypted reconstructed database and output file; import requires staging, validation database, destination/WAL, and merge space. There is no claimed device benchmark or fixed disk-space multiplier. A full/unavailable device fails the operation rather than deleting or replacing the archive key.

Owned temporary directories are private, excluded from system backup, and protected with iOS complete Data Protection; files use mode `0600` and directories `0700`. System `/var` and `/tmp` aliases are resolved to physical paths before SQLite's all-component `NOFOLLOW` check; arbitrary input symlinks, special files, oversize files and unsupported path parents are rejected. The input file descriptor is checked for size/inode/device/mtime changes after copying. System provider imports that expose unsupported paths fail closed. The completed encrypted export stays owned until the picker completes or cancels. Ciphertext staging is removed on release/failure; iOS may also remove temporary files after termination. No temporary plaintext is left for recovery.

Cancellation is checked between bounded file/media chunks, rows and SQLite progress callbacks. Password derivation is fixed-cost and cannot be interrupted inside CommonCrypto. Backgrounding releases the UI read lease and requests cancellation. Protected-data loss, account disposal or changed retention/privacy/purge preferences invalidate the coordinator's transfer epoch. Cancellation after a transaction has committed cannot undo it. Cancellation while initializing an explicitly authorized fresh destination can leave an empty encrypted database/key; it never overwrites an existing key. Interrupted export files are removed and interrupted merges roll back. Export may already have applied the account's normal retention policy even if a later export step fails.

## Qualification evidence and remaining checks

`ArchiveTransferTests` exercises real SQLCipher encrypted stores: text/edit/media round trips and reopen, same-user local-account remapping, wrong password/owner/environment, altered/truncated files, authenticated malformed schema/oversize rows, idempotent merge, conflicts/rollback, retention, preserved destination generation/outbox receipts, cancellation during actual media writes, concurrent WAL snapshot consistency, FTS purge-token exclusion, real temporary paths, global plaintext-header defaults, and existing-file/symlink rejection. `LocalArchiveTransferTests` exercises real encrypted Postbox settings plus the coordinator/store facade, key-provider call counts, new-destination initialization after authentication, capture/lease/lock gates, native-media reads, export and restart. `LocalArchiveTransferLifecycleTests` covers foreground transitions with a covered picker/password presenter and releasing access when navigation leaves the screen. These tests are authored; do not substitute this list for the latest macOS execution result.

Visual acceptance must record both picker paths in light and dark mode. Before recording, expected frames are: ordinary native grouped rows with no clipped explanatory text; system password alerts obscure typed characters; opening/cancelling a picker returns to the same account; busy state disables overlapping transfer/capture actions; cancellation restores idle controls; repeated navigation/backgrounding leaves no stale picker, plaintext preview, or password in a status row. VoiceOver labels, Dynamic Type wrapping, actual file-provider permissions, locked-device rejection, termination recovery and physical-device Keychain behavior still need native verification. No screenshot/recording or physical-device outcome is asserted by this document.
