// SPDX-License-Identifier: GPL-2.0-or-later
// Native archive draft, 2026-11-04. No networking or Telegram update hooks.
import Foundation
import Darwin
import sqlcipher

private let archiveTransient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
private enum SQLValue { case integer(Int64), optionalInteger(Int64?), text(String), blob(Data) }

/// One account, one encrypted database, one serial worker. Completions use callbackQueue.
public final class ArchiveStore {
    public let accountId: Int64
    public let databaseURL: URL
    private let databasePath: String
    private let worker: DispatchQueue
    private let callbackQueue: DispatchQueue
    private let clock: () -> Int64
    private var handle: OpaquePointer?
    private var retention: ArchiveRetentionPolicy

    private init(accountId: Int64, databasePath: String, worker: DispatchQueue,
                 callbackQueue: DispatchQueue, retention: ArchiveRetentionPolicy, clock: @escaping () -> Int64) {
        self.accountId = accountId; self.databasePath = databasePath
        self.databaseURL = URL(fileURLWithPath: databasePath); self.worker = worker
        self.callbackQueue = callbackQueue; self.retention = retention; self.clock = clock
    }

    public static func open(baseDirectory: URL, accountId: Int64, keyProvider: ArchiveKeyProvider,
                            createIfMissing: Bool = true,
                            retention: ArchiveRetentionPolicy = ArchiveRetentionPolicy(),
                            callbackQueue: DispatchQueue = .main,
                            clock: @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970) },
                            completion: @escaping (Result<ArchiveStore, ArchiveError>) -> Void) {
        let worker = DispatchQueue(label: "LocalMessageArchive.storage", qos: .utility)
        worker.async {
            let result: Result<ArchiveStore, ArchiveError>
            do {
                try validateRetention(retention)
                guard baseDirectory.isFileURL else { throw ArchiveError.invalidInput }
                let requestedRoot = baseDirectory.standardizedFileURL
                if !createIfMissing {
                    let database = requestedRoot.appendingPathComponent("account-\(accountId)/archive.sqlite")
                    let files = [database] + ["-wal", "-shm", "-journal"].map { URL(fileURLWithPath: database.path + $0) }
                    let existing = try files.filter { try entryExists($0) }
                    guard existing.contains(database) else {
                        // An orphan WAL/journal is damage, not an empty archive.
                        guard existing.isEmpty else { throw ArchiveError.invalidInput }
                        throw ArchiveError.missingArchive
                    }
                }
                // Validate the caller-owned endpoint first, then resolve system parent aliases
                // such as macOS /var -> /private/var for SQLITE_OPEN_NOFOLLOW.
                try protectedDirectory(requestedRoot)
                // Foundation resolvingSymlinksInPath can preserve a /var alias on macOS.
                // Keep the POSIX physical pathname for SQLite's all-components NOFOLLOW.
                let root = URL(fileURLWithPath: try physicalPath(requestedRoot), isDirectory: true)
                let directory = root.appendingPathComponent("account-\(accountId)", isDirectory: true)
                try protectedDirectory(directory)
                let databasePath = try physicalPath(directory) + "/archive.sqlite"
                let url = URL(fileURLWithPath: databasePath)
                let files = [url] + ["-wal", "-shm", "-journal"].map { URL(fileURLWithPath: databasePath + $0) }
                let existingFiles = try files.filter { try entryExists($0) }
                let existed = existingFiles.contains(url)
                guard existed || createIfMissing else { throw ArchiveError.missingArchive }
                // Orphan sidecars may represent an interrupted/lost database. Never overwrite them.
                guard existed || existingFiles.isEmpty else {
                    throw ArchiveError.invalidInput
                }
                for file in files where try entryExists(file) { try protectFile(file) }
                let key = try keyProvider.key(accountId: accountId, createIfMissing: !existed)
                guard key.count == 32 else { throw ArchiveError.invalidKey }
                let store = ArchiveStore(accountId: accountId, databasePath: databasePath, worker: worker,
                                         callbackQueue: callbackQueue, retention: retention, clock: clock)
                do {
                    try store.openDatabase(key: key, createIfMissing: createIfMissing)
                    for file in files where try entryExists(file) { try protectFile(file) }
                } catch {
                    if let handle = store.handle { sqlite3_close_v2(handle); store.handle = nil }
                    throw error
                }
                result = .success(store)
            } catch { result = .failure(normalized(error)) }
            callbackQueue.async { completion(result) }
        }
    }

    deinit {
        if let handle = handle { worker.async { sqlite3_close_v2(handle) } }
    }

    public func close(completion: @escaping () -> Void = {}) {
        worker.async {
            if let handle = self.handle { sqlite3_close_v2(handle); self.handle = nil }
            self.callbackQueue.async(execute: completion)
        }
    }

    /// Exports this archive alone, including its encrypted media BLOBs. A fresh
    /// transport key protects the snapshot; the account's Keychain key is never
    /// exported. The destination must be a new file in a caller-owned directory.
    public func exportBackup(to destination: URL, password: String, ownerId: Int64? = nil, testingEnvironment: Bool = false,
                             cancellation: ArchiveTransferCancellation,
                             completion: @escaping (Result<ArchiveTransferExport, ArchiveTransferError>) -> Void) {
        self.worker.async {
            let result: Result<ArchiveTransferExport, ArchiveTransferError>
            do {
                guard let handle = self.handle else { throw ArchiveTransferError.unavailable }
                try ArchiveTransferEnvelope.checkPassword(password)
                try cancellation.check()
                let scratch = try ArchiveTransferFiles.scratchDirectory()
                defer { try? FileManager.default.removeItem(at: scratch) }
                ArchiveTransferDatabase.installCancellation(handle, cancellation)
                defer { ArchiveTransferDatabase.clearCancellation(handle) }
                try Self.validateTransferDatabase(handle, accountId: self.accountId, directory: scratch, cancellation: cancellation)
                // Reads normally enforce this policy too. Do not export expired
                // entries merely because the account has been idle.
                _ = try self.transferTransaction(handle: handle, cancellation: cancellation) {
                    try self.prune(now: self.clock(), policy: self.retention)
                }
                let snapshot = scratch.appendingPathComponent("snapshot.sqlite")
                let key = ArchiveTransferEnvelope.randomKey()
                try Self.writeTransferSnapshot(source: handle, destination: snapshot, key: key,
                    accountId: self.accountId, now: self.clock(), cancellation: cancellation)
                result = .success(try ArchiveTransferEnvelope.seal(snapshot: snapshot, destination: destination,
                    accountId: self.accountId, ownerId: ownerId ?? self.accountId, testingEnvironment: testingEnvironment,
                    transportKey: key, password: password, cancellation: cancellation))
            } catch { result = .failure(Self.transferError(error, cancellation: cancellation)) }
            self.callbackQueue.async { completion(result) }
        }
    }

    /// Explicit manual restore merges immutable history into this account only.
    /// Conflicts abort the entire transaction. Capture preferences, purge
    /// generations and outbox receipts are never taken from a backup.
    public func importBackup(from source: URL, password: String, ownerId: Int64? = nil, testingEnvironment: Bool = false, expectedGeneration: Int64,
                             cancellation: ArchiveTransferCancellation,
                             completion: @escaping (Result<ArchiveTransferImport, ArchiveTransferError>) -> Void) {
        self.worker.async {
            let result: Result<ArchiveTransferImport, ArchiveTransferError>
            do {
                guard let handle = self.handle else { throw ArchiveTransferError.unavailable }
                let prepared = try Self.prepareBackup(source: source, password: password,
                    ownerId: ownerId ?? self.accountId, testingEnvironment: testingEnvironment, cancellation: cancellation)
                result = .success(try self.mergePreparedBackup(prepared, handle: handle,
                    ownerId: ownerId ?? self.accountId, testingEnvironment: testingEnvironment, expectedGeneration: expectedGeneration, cancellation: cancellation))
            } catch { result = .failure(Self.transferError(error, cancellation: cancellation)) }
            self.callbackQueue.async { completion(result) }
        }
    }

    /// Authenticates and validates before a coordinator creates any destination
    /// archive/key. This also permits restore on a fresh installation of the
    /// same Telegram account, whose local AccountRecordId may have changed.
    public static func prepareImport(from source: URL, password: String, ownerId: Int64, testingEnvironment: Bool = false,
                                     cancellation: ArchiveTransferCancellation,
                                     callbackQueue: DispatchQueue = .main,
                                     completion: @escaping (Result<ArchivePreparedImport, ArchiveTransferError>) -> Void) {
        DispatchQueue(label: "LocalMessageArchive.backup-prepare", qos: .utility).async {
            let result: Result<ArchivePreparedImport, ArchiveTransferError>
            do { result = .success(try self.prepareBackup(source: source, password: password, ownerId: ownerId, testingEnvironment: testingEnvironment, cancellation: cancellation)) }
            catch { result = .failure(self.transferError(error, cancellation: cancellation)) }
            callbackQueue.async { completion(result) }
        }
    }

    public func importPreparedBackup(_ prepared: ArchivePreparedImport, ownerId: Int64, testingEnvironment: Bool = false,
                                     expectedGeneration: Int64, cancellation: ArchiveTransferCancellation,
                                     completion: @escaping (Result<ArchiveTransferImport, ArchiveTransferError>) -> Void) {
        self.worker.async {
            let result: Result<ArchiveTransferImport, ArchiveTransferError>
            do {
                guard let handle = self.handle else { throw ArchiveTransferError.unavailable }
                result = .success(try self.mergePreparedBackup(prepared, handle: handle, ownerId: ownerId, testingEnvironment: testingEnvironment,
                    expectedGeneration: expectedGeneration, cancellation: cancellation))
            } catch { result = .failure(Self.transferError(error, cancellation: cancellation)) }
            self.callbackQueue.async { completion(result) }
        }
    }

    private static func prepareBackup(source: URL, password: String, ownerId: Int64, testingEnvironment: Bool,
                                      cancellation: ArchiveTransferCancellation) throws -> ArchivePreparedImport {
        try ArchiveTransferEnvelope.checkPassword(password)
        try cancellation.check()
        let scratch = try ArchiveTransferFiles.scratchDirectory()
        var opened: OpaquePointer?
        do {
            let snapshot = scratch.appendingPathComponent("snapshot.sqlite")
            let unlocked = try ArchiveTransferEnvelope.unseal(source: source, snapshot: snapshot,
                ownerId: ownerId, testingEnvironment: testingEnvironment, password: password, cancellation: cancellation)
            let input = try ArchiveTransferDatabase.openSnapshot(snapshot, key: unlocked.key, cancellation: cancellation)
            opened = input
            try self.validateTransferDatabase(input, accountId: unlocked.accountId, directory: scratch, cancellation: cancellation)
            try cancellation.check()
            return ArchivePreparedImport(database: input, sourceAccountId: unlocked.accountId, ownerId: ownerId, testingEnvironment: testingEnvironment,
                                          directory: scratch, cancellation: cancellation)
        } catch {
            if let opened { sqlite3_close_v2(opened) }
            try? FileManager.default.removeItem(at: scratch)
            throw error
        }
    }

    /// Compare against the actual current module's freshly generated schema,
    /// using only a random-key encrypted TEMP database. No Keychain provider or
    /// destination account directory is consulted during this validation.
    private static func validateTransferDatabase(_ input: OpaquePointer, accountId: Int64, directory: URL,
                                                  cancellation: ArchiveTransferCancellation) throws {
        let file = directory.appendingPathComponent("schema-validation.sqlite")
        let fd = try ArchiveTransferFiles.create(file); Darwin.close(fd)
        let worker = DispatchQueue(label: "LocalMessageArchive.backup-schema", qos: .utility)
        try worker.sync {
            let store = ArchiveStore(accountId: accountId, databasePath: file.path, worker: worker,
                callbackQueue: .global(qos: .utility), retention: ArchiveRetentionPolicy(),
                clock: { Int64(Date().timeIntervalSince1970) })
            defer {
                if let handle = store.handle { sqlite3_close_v2(handle); store.handle = nil }
            }
            try store.openDatabase(key: ArchiveTransferEnvelope.randomKey(), createIfMissing: true)
            guard let expected = store.handle else { throw ArchiveTransferError.storageUnavailable }
            try ArchiveTransferDatabase.validate(source: input, destination: expected, accountId: accountId, cancellation: cancellation)
            try ArchiveTransferDatabase.validateBasic(source: input, accountId: accountId, cancellation: cancellation)
        }
    }

    /// Reconstruct only retained logical rows. A physical backup would also
    /// carry deleted FTS segments and freelist pages outside the export scope.
    private static func writeTransferSnapshot(source: OpaquePointer, destination: URL, key: Data,
                                               accountId: Int64, now: Int64,
                                               cancellation: ArchiveTransferCancellation) throws {
        let fd = try ArchiveTransferFiles.create(destination); Darwin.close(fd)
        let physical = try ArchiveTransferFiles.physicalURL(destination)
        let worker = DispatchQueue(label: "LocalMessageArchive.backup-snapshot", qos: .utility)
        try worker.sync {
            let target = ArchiveStore(accountId: accountId, databasePath: physical.path, worker: worker,
                callbackQueue: .global(qos: .utility), retention: ArchiveRetentionPolicy(), clock: { now })
            defer { if let handle = target.handle { sqlite3_close_v2(handle); target.handle = nil } }
            try target.openDatabase(key: key, createIfMissing: true)
            guard let output = target.handle else { throw ArchiveTransferError.storageUnavailable }
            ArchiveTransferDatabase.installCancellation(output, cancellation)
            defer { ArchiveTransferDatabase.clearCancellation(output) }
            try ArchiveTransferDatabase.execute(source, "BEGIN")
            defer {
                ArchiveTransferDatabase.clearCancellation(source)
                try? ArchiveTransferDatabase.execute(source, "ROLLBACK")
            }
            // Establish the source WAL read view before another connection can
            // commit a new version while the bounded row/blob copies proceed.
            try ArchiveTransferDatabase.validate(source: source, destination: output, accountId: accountId, cancellation: cancellation)
            _ = try target.transferTransaction(handle: output, cancellation: cancellation) {
                try ArchiveTransferDatabase.merge(source: source, destination: output,
                    sourceAccountId: accountId, accountId: accountId, now: now, cancellation: cancellation)
            }
            // A completed payload stands alone, with no WAL sidecar required.
            try ArchiveTransferDatabase.execute(output, "PRAGMA journal_mode=DELETE")
            try cancellation.check()
        }
    }

    private func mergePreparedBackup(_ prepared: ArchivePreparedImport, handle: OpaquePointer,
                                      ownerId: Int64, testingEnvironment: Bool, expectedGeneration: Int64,
                                      cancellation: ArchiveTransferCancellation) throws -> ArchiveTransferImport {
        guard prepared.ownerId == ownerId, prepared.testingEnvironment == testingEnvironment, prepared.cancellation === cancellation else {
            throw ArchiveTransferError.wrongAccount
        }
        try cancellation.check()
        ArchiveTransferDatabase.installCancellation(handle, cancellation)
        defer { ArchiveTransferDatabase.clearCancellation(handle) }
        try ArchiveTransferDatabase.validate(source: prepared.database, destination: handle,
            accountId: prepared.sourceAccountId, cancellation: cancellation)
        return try self.transferTransaction(handle: handle, cancellation: cancellation) {
            guard expectedGeneration >= 0, try self.captureGeneration() == expectedGeneration else {
                throw ArchiveTransferError.unavailable
            }
            let merged = try ArchiveTransferDatabase.merge(source: prepared.database, destination: handle,
                sourceAccountId: prepared.sourceAccountId, accountId: self.accountId,
                now: self.clock(), cancellation: cancellation)
            let removed = try self.prune(now: self.clock(), policy: self.retention)
            try cancellation.check()
            return ArchiveTransferImport(addedVersions: merged.added, duplicateVersions: merged.duplicates,
                                         addedMedia: merged.media, removedByRetention: removed)
        }
    }

    private func transferTransaction<Value>(handle: OpaquePointer, cancellation: ArchiveTransferCancellation,
                                             operation: () throws -> Value) throws -> Value {
        try self.execute("BEGIN IMMEDIATE")
        do {
            let value = try operation()
            try cancellation.checkpoint(.beforeCommit)
            try self.execute("COMMIT")
            return value
        } catch {
            // A cancelled progress handler must not interrupt the rollback that
            // protects the pre-existing archive.
            ArchiveTransferDatabase.clearCancellation(handle)
            try? self.execute("ROLLBACK")
            throw error
        }
    }

    private static func transferError(_ error: Error, cancellation: ArchiveTransferCancellation) -> ArchiveTransferError {
        if cancellation.isCancelled { return .cancelled }
        if let error = error as? ArchiveTransferError { return error }
        if let error = error as? ArchiveError {
            switch error {
            case .accountMismatch: return .wrongAccount
            case .eventConflict: return .conflict
            case .invalidInput: return .invalidArchive
            case .unsupportedSchema: return .unsupportedVersion
            default: return .storageUnavailable
            }
        }
        return .storageUnavailable
    }

    /// Commit all eligible events atomically. An event-key collision with different content rolls back the batch.
    public func record(_ captures: [ArchiveCapture], generation: Int64, policy: ArchiveCapturePolicy,
                       completion: @escaping (Result<ArchiveWriteResult, ArchiveError>) -> Void) {
        submit(completion) {
            let now = self.clock()
            guard now >= 0, generation >= 0, captures.count <= 256 else { throw ArchiveError.invalidInput }
            var bytes: Int64 = 0
            for capture in captures {
                try self.validate(capture.event, now: now)
                bytes += capture.event.logicalBytes
            }
            guard bytes <= 8 * 1024 * 1024 else { throw ArchiveError.invalidInput }
            return try self.transaction {
                let current = try self.captureGeneration()
                guard generation == current else { throw ArchiveError.generationMismatch(expected: current, received: generation) }
                return try self.recordLocked(captures, now: now, policy: policy)
            }
        }
    }

    /// Consume one item from this account's single monotonically indexed outbox.
    /// The caller must drain ascending indices and must never reuse/reset an index within a generation.
    /// A receipt survives event retention, so a crash before the caller's acknowledgement cannot
    /// resurrect an evicted snapshot. Policy exclusions are also durably consumed.
    public func recordFromOutbox(_ capture: ArchiveCapture, index: Int64, generation: Int64,
                                 policy: ArchiveCapturePolicy,
                                 completion: @escaping (Result<ArchiveWriteResult, ArchiveError>) -> Void) {
        submit(completion) {
            let now = self.clock()
            guard now >= 0, index >= 0, generation >= 0 else { throw ArchiveError.invalidInput }
            try self.validate(capture.event, now: now)
            return try self.transaction {
                let current = try self.captureGeneration()
                guard generation == current else { throw ArchiveError.generationMismatch(expected: current, received: generation) }
                let receipt = try self.outboxReceipt()
                if index <= receipt.index {
                    // Detect accidental reuse at the current boundary. Older indices have already
                    // been consumed under the caller's ordering contract; retain no unbounded ledger.
                    if index == receipt.index, capture.event.eventKey != receipt.eventKey {
                        throw ArchiveError.eventConflict
                    }
                    if let existing = try self.read("WHERE event_key = ?", [.text(capture.event.eventKey)]).first,
                       existing.event != capture.event {
                        throw ArchiveError.eventConflict
                    }
                    return ArchiveWriteResult(inserted: 0, duplicates: 1, excluded: 0, removedByRetention: 0)
                }
                let result = try self.recordLocked([capture], now: now, policy: policy)
                try self.execute("UPDATE archive_meta SET outbox_high_water=?,outbox_last_event_key=? WHERE id=1",
                                 [.integer(index), .text(capture.event.eventKey)])
                guard sqlite3_changes(self.handle) == 1 else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
                return result
            }
        }
    }

    /// Called only under the caller's IMMEDIATE transaction.
    private func recordLocked(_ captures: [ArchiveCapture], now: Int64, policy: ArchiveCapturePolicy) throws -> ArchiveWriteResult {
        var inserted = 0, duplicates = 0, excluded = 0
        for capture in captures {
            let event = capture.event
            guard policy.allows(event, context: capture.context) else { excluded += 1; continue }
            if let existing = try read("WHERE event_key = ?", [.text(event.eventKey)]).first {
                guard existing.event == event else { throw ArchiveError.eventConflict }
                duplicates += 1
            } else {
                try execute("""
                    INSERT INTO events(event_key,account_id,peer_id,namespace,message_id,kind,observed_at,
                    server_edit_at,text,author_id,sent_at,entities,payload_bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, [.text(event.eventKey), .integer(accountId), .integer(event.message.peerId),
                           .integer(Int64(event.message.namespace)), .integer(Int64(event.message.messageId)),
                           .integer(Int64(event.kind.rawValue)), .integer(event.observedAt), .optionalInteger(event.serverEditAt),
                           .text(event.content.text), .optionalInteger(event.content.authorId), .integer(event.content.sentAt),
                           .blob(event.content.entities), .integer(event.logicalBytes)])
                inserted += 1
            }
        }
        let removed = try prune(now: now, policy: retention)
        return ArchiveWriteResult(inserted: inserted, duplicates: duplicates, excluded: excluded, removedByRetention: removed)
    }

    /// Ordered by local capture sequence, oldest first within this page; server edit time remains separate metadata.
    public func timeline(for message: ArchiveMessageID, beforeSequence: Int64? = nil, limit: Int = 100,
                         completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
        submit(completion) {
            guard message.accountId == self.accountId else { throw ArchiveError.accountMismatch }
            try Self.validatePage(beforeSequence, limit)
            return Array(try self.read("WHERE account_id=? AND peer_id=? AND namespace=? AND message_id=? AND sequence<? ORDER BY sequence DESC LIMIT ?",
                                 [.integer(self.accountId), .integer(message.peerId), .integer(Int64(message.namespace)),
                                  .integer(Int64(message.messageId)), .integer(beforeSequence ?? Int64.max), .integer(Int64(limit))]).reversed())
        }
    }

    public func deletedMessages(peerId: Int64? = nil, beforeSequence: Int64? = nil, limit: Int = 100,
                                completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
        submit(completion) {
            try Self.validatePage(beforeSequence, limit)
            var clause = "WHERE account_id=? AND kind=1 AND sequence<?"
            var values: [SQLValue] = [.integer(self.accountId), .integer(beforeSequence ?? Int64.max)]
            if let peerId = peerId { clause += " AND peer_id=?"; values.append(.integer(peerId)) }
            clause += " ORDER BY sequence DESC LIMIT ?"; values.append(.integer(Int64(limit)))
            return try self.read(clause, values)
        }
    }

    /// Literal word/phrase search. FTS syntax is quoted, never executed from user input.
    /// The index and its content remain inside this account's encrypted database.
    public func searchMessages(query: String, peerId: Int64? = nil, kind: ArchiveEventKind? = nil,
                               beforeSequence: Int64? = nil, limit: Int = 100,
                               completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
        submit(completion) {
            try Self.validatePage(beforeSequence, limit)
            let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !query.isEmpty, query.utf8.count <= 512, !query.contains("\0") else { throw ArchiveError.invalidInput }
            let now = self.clock()
            guard now >= 0 else { throw ArchiveError.invalidInput }
            _ = try self.transaction { try self.prune(now: now, policy: self.retention) }
            let phrase = "\"" + query.replacingOccurrences(of: "\"", with: "\"\"") + "\""
            var clause = "WHERE account_id=? AND sequence<? AND sequence IN (SELECT rowid FROM events_search WHERE events_search MATCH ?)"
            var values: [SQLValue] = [.integer(self.accountId), .integer(beforeSequence ?? Int64.max), .text(phrase)]
            if let peerId { clause += " AND peer_id=?"; values.append(.integer(peerId)) }
            if let kind { clause += " AND kind=?"; values.append(.integer(Int64(kind.rawValue))) }
            clause += " ORDER BY sequence DESC LIMIT ?"
            values.append(.integer(Int64(limit)))
            return try self.read(clause, values)
        }
    }

    public func mediaItems(peerId: Int64? = nil, kind: String? = nil,
                           beforeSequence: Int64? = nil, beforeSlot: Int? = nil, limit: Int = 60,
                           completion: @escaping (Result<[ArchivedMediaItem], ArchiveError>) -> Void) {
        submit(completion) {
            try Self.validatePage(beforeSequence, limit)
            guard (beforeSequence == nil) == (beforeSlot == nil),
                  beforeSlot.map({ (0..<10).contains($0) }) ?? true,
                  kind.map({ !$0.isEmpty && $0.utf8.count <= 32 && !$0.contains("\0") }) ?? true else { throw ArchiveError.invalidInput }
            let now = self.clock()
            guard now >= 0 else { throw ArchiveError.invalidInput }
            _ = try self.transaction { try self.prune(now: now, policy: self.retention) }
            var sql = """
                SELECT e.sequence,e.event_key,e.peer_id,e.namespace,e.message_id,e.kind,e.observed_at,substr(e.text,1,160),m.slot,m.kind,m.mime,length(m.payload)
                FROM media_gallery g CROSS JOIN events e ON e.sequence=g.sequence CROSS JOIN event_media m ON m.event_key=g.event_key AND m.slot=g.slot
                WHERE e.account_id=? AND (g.sequence,g.slot)<(?,?)
                """
            let cursor = beforeSequence ?? Int64.max
            var values: [SQLValue] = [.integer(self.accountId), .integer(cursor), .integer(Int64(beforeSlot ?? 10))]
            if let peerId { sql += " AND e.peer_id=?"; values.append(.integer(peerId)) }
            if let kind { sql += " AND g.kind=?"; values.append(.text(kind)) }
            sql += " ORDER BY g.sequence DESC,g.slot DESC LIMIT ?"
            values.append(.integer(Int64(limit)))
            let row = try self.statement(sql, values)
            defer { sqlite3_finalize(row) }
            var items: [ArchivedMediaItem] = []
            var code = sqlite3_step(row)
            while code == SQLITE_ROW {
                guard let eventKind = ArchiveEventKind(rawValue: sqlite3_column_int(row, 5)) else { throw ArchiveError.invalidInput }
                let slot = Int(sqlite3_column_int(row, 8)), size = sqlite3_column_int64(row, 11)
                guard (0..<10).contains(slot), size >= 0, size <= 512 * 1024 * 1024 else { throw ArchiveError.invalidInput }
                items.append(ArchivedMediaItem(sequence: sqlite3_column_int64(row, 0), eventKey: try self.text(row, 1),
                    message: ArchiveMessageID(accountId: self.accountId, peerId: sqlite3_column_int64(row, 2), namespace: sqlite3_column_int(row, 3), messageId: sqlite3_column_int(row, 4)),
                    eventKind: eventKind, observedAt: sqlite3_column_int64(row, 6), caption: try self.text(row, 7), slot: slot,
                    kind: try self.text(row, 9), mimeType: try self.text(row, 10), byteCount: size))
                code = sqlite3_step(row)
            }
            guard code == SQLITE_DONE else { throw ArchiveError.unavailable(code) }
            return items
        }
    }

    public func usage(completion: @escaping (Result<ArchiveUsage, ArchiveError>) -> Void) {
        submit(completion) { try self.currentUsage() }
    }

    public func setRetention(_ policy: ArchiveRetentionPolicy, completion: @escaping (Result<Int, ArchiveError>) -> Void) {
        submit(completion) {
            try Self.validateRetention(policy)
            let now = self.clock()
            guard now >= 0 else { throw ArchiveError.invalidInput }
            let removed = try self.transaction { try self.prune(now: now, policy: policy) }
            self.retention = policy
            return removed
        }
    }

    /// Deletes one local snapshot and its indexed media. Keep the outbox receipt
    /// so a retry after the archive-commit/ack gap cannot resurrect this version.
    public func deleteSnapshot(eventKey: String, generation: Int64,
                               completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        submit(completion) {
            try Self.validateMediaLookup(eventKey, 0)
            guard generation >= 0 else { throw ArchiveError.invalidInput }
            return try self.transaction {
                let current = try self.captureGeneration()
                guard generation == current else { throw ArchiveError.generationMismatch(expected: current, received: generation) }
                try self.execute("DELETE FROM events WHERE event_key=?", [.text(eventKey)])
                // Missing is an acknowledged retry of a local deletion.
                return true
            }
        }
    }

    /// A coordinator must stamp this generation at capture time and preserve it through outbox retries.
    public func currentGeneration(completion: @escaping (Result<Int64, ArchiveError>) -> Void) {
        submit(completion) { try self.captureGeneration() }
    }

    /// Advances exactly once. A same-generation retry never erases records accepted after that purge.
    /// Does not delete Telegram cache/session data or the account's key.
    public func purge(advancingToGeneration target: Int64, completion: @escaping (Result<ArchivePurgeResult, ArchiveError>) -> Void) {
        submit(completion) {
            guard target >= 0 else { throw ArchiveError.invalidInput }
            return try self.transaction {
                let current = try self.captureGeneration()
                if target == current { return ArchivePurgeResult(generation: current, didPurge: false) }
                guard current < Int64.max else { throw ArchiveError.invalidInput }
                let next = current + 1
                guard target == next else { throw ArchiveError.generationMismatch(expected: next, received: target) }
                try self.execute("DELETE FROM events")
                try self.execute("UPDATE archive_meta SET capture_generation=?,outbox_high_water=-1,outbox_last_event_key='' WHERE id=1", [.integer(target)])
                guard sqlite3_changes(self.handle) == 1 else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
                return ArchivePurgeResult(generation: target, didPurge: true)
            }
        }
    }

    private func openDatabase(key: Data, createIfMissing: Bool) throws {
        var opened: OpaquePointer?
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX | SQLITE_OPEN_NOFOLLOW | (createIfMissing ? SQLITE_OPEN_CREATE : 0)
        let code = sqlite3_open_v2(databasePath, &opened, flags, nil)
        guard code == SQLITE_OK, let opened = opened else {
            if let opened = opened { sqlite3_close_v2(opened) }
            throw ArchiveError.unavailable(code)
        }
        handle = opened
        sqlite3_busy_timeout(opened, 1000)
        // Only fixed-format hex from exactly 32 key bytes is interpolated. Never log this statement.
        let hex = key.map { String(format: "%02x", $0) }.joined()
        try execute("PRAGMA key = \"x'\(hex)'\"")
        // Postbox changes the process default to 32. Override this connection AFTER key setup.
        try execute("PRAGMA cipher_plaintext_header_size = 0")
        let cipher = try textScalar("PRAGMA cipher_version")
        guard !cipher.isEmpty, try scalar("PRAGMA cipher_plaintext_header_size") == 0 else { throw ArchiveError.invalidKey }
        _ = try scalar("SELECT COUNT(*) FROM sqlite_master") // Authenticate existing pages before mutation.
        try execute("PRAGMA temp_store=MEMORY")
        guard try scalar("PRAGMA temp_store") == 2 else { throw ArchiveError.protectionUnavailable }
        try execute("PRAGMA foreign_keys=ON")
        guard try scalar("PRAGMA foreign_keys") == 1 else { throw ArchiveError.protectionUnavailable }
        let version = try scalar("PRAGMA user_version")
        guard (0...8).contains(version) else { throw ArchiveError.unsupportedSchema(Int(version)) }
        try transaction {
            // Another connection may have migrated while this one waited for the writer lock.
            let lockedVersion = try scalar("PRAGMA user_version")
            guard (0...8).contains(lockedVersion) else { throw ArchiveError.unsupportedSchema(Int(lockedVersion)) }
            if lockedVersion == 0 {
                guard try scalar("SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'") == 0 else {
                    throw ArchiveError.unsupportedSchema(0)
                }
                try createInitialSchema()
            } else {
                // Validate identity inside the migration transaction before any schema mutation.
                guard try scalar("SELECT account_id FROM archive_meta WHERE id=1") == accountId else { throw ArchiveError.accountMismatch }
            }
            if lockedVersion < 2 { try migrateUsageToVersion2() }
            if lockedVersion < 3 { try migrateGenerationToVersion3() }
            if lockedVersion < 4 { try migrateOutboxReceiptToVersion4() }
            if lockedVersion < 5 { try migrateMediaToVersion5() }
            if lockedVersion < 6 { try migrateSearchToVersion6() }
            if lockedVersion < 7 { try migrateMediaToVersion7() }
            if lockedVersion < 8 { try migrateGalleryToVersion8() }
            _ = try currentUsage()
            _ = try captureGeneration()
            _ = try outboxReceipt()
        }
        try execute("PRAGMA journal_mode=WAL")
        try execute("PRAGMA synchronous=FULL")
        try execute("PRAGMA secure_delete=ON")
        let now = clock()
        guard now >= 0 else { throw ArchiveError.invalidInput }
        _ = try transaction { try prune(now: now, policy: retention) }
    }

    private func createInitialSchema() throws {
        try execute("CREATE TABLE archive_meta(id INTEGER PRIMARY KEY CHECK(id=1),account_id INTEGER NOT NULL)")
        try execute("INSERT INTO archive_meta VALUES(1,?)", [.integer(accountId)])
        try execute("""
            CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL,peer_id INTEGER NOT NULL,namespace INTEGER NOT NULL,message_id INTEGER NOT NULL,
            kind INTEGER NOT NULL CHECK(kind IN(1,2)),observed_at INTEGER NOT NULL,server_edit_at INTEGER,
            text TEXT NOT NULL,author_id INTEGER,sent_at INTEGER NOT NULL,entities BLOB NOT NULL,payload_bytes INTEGER NOT NULL)
            """)
        try execute("CREATE INDEX events_message ON events(account_id,peer_id,namespace,message_id,sequence)")
        try execute("CREATE INDEX events_deleted ON events(account_id,kind,sequence)")
        try execute("CREATE INDEX events_retention ON events(observed_at,sequence)")
    }

    /// One historical scan during migration; subsequent writes use transactional counters.
    private func migrateUsageToVersion2() throws {
        let invalid = try scalar("""
            SELECT COUNT(*) FROM events WHERE typeof(payload_bytes)!='integer' OR payload_bytes<0
            OR payload_bytes!=64+length(CAST(text AS BLOB))+length(entities)+length(CAST(event_key AS BLOB))
            """)
        guard invalid == 0 else { throw ArchiveError.invalidInput }
        guard try scalar("SELECT COUNT(*) FROM events WHERE account_id!=?", [.integer(accountId)]) == 0 else { throw ArchiveError.accountMismatch }
        try execute("""
            CREATE TABLE archive_usage(id INTEGER PRIMARY KEY CHECK(id=1),
            record_count INTEGER NOT NULL CHECK(typeof(record_count)='integer' AND record_count>=0),
            payload_bytes INTEGER NOT NULL CHECK(typeof(payload_bytes)='integer' AND payload_bytes>=0))
            """)
        try execute("INSERT INTO archive_usage SELECT 1,COUNT(*),COALESCE(SUM(payload_bytes),0) FROM events")
        try execute("""
            CREATE TRIGGER archive_events_validate_insert BEFORE INSERT ON events
            WHEN typeof(NEW.payload_bytes)!='integer' OR NEW.payload_bytes<0
            OR NEW.payload_bytes!=64+length(CAST(NEW.text AS BLOB))+length(NEW.entities)+length(CAST(NEW.event_key AS BLOB))
            BEGIN SELECT RAISE(ABORT,'Invalid archive payload size'); END
            """)
        try execute("""
            CREATE TRIGGER archive_events_insert_usage AFTER INSERT ON events BEGIN
            UPDATE archive_usage SET record_count=record_count+1,payload_bytes=payload_bytes+NEW.payload_bytes WHERE id=1;
            END
            """)
        try execute("""
            CREATE TRIGGER archive_events_delete_usage AFTER DELETE ON events BEGIN
            UPDATE archive_usage SET record_count=record_count-1,payload_bytes=payload_bytes-OLD.payload_bytes WHERE id=1;
            END
            """)
        try execute("""
            CREATE TRIGGER archive_events_immutable BEFORE UPDATE ON events BEGIN
            SELECT RAISE(ABORT,'Archive events are immutable'); END
            """)
        try execute("PRAGMA user_version=2")
    }

    private func currentUsage() throws -> ArchiveUsage {
        let prepared = try statement("SELECT record_count,payload_bytes FROM archive_usage WHERE id=1", [])
        defer { sqlite3_finalize(prepared) }
        let code = sqlite3_step(prepared)
        guard code == SQLITE_ROW else { throw ArchiveError.unavailable(code == SQLITE_DONE ? SQLITE_CORRUPT : code) }
        let records = sqlite3_column_int64(prepared, 0), bytes = sqlite3_column_int64(prepared, 1)
        guard sqlite3_column_type(prepared, 0) == SQLITE_INTEGER, sqlite3_column_type(prepared, 1) == SQLITE_INTEGER,
              records >= 0, bytes >= 0 else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
        return ArchiveUsage(records: records, logicalPayloadBytes: bytes)
    }

    private func migrateGenerationToVersion3() throws {
        try execute("""
            ALTER TABLE archive_meta ADD COLUMN capture_generation INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(capture_generation)='integer' AND capture_generation>=0)
            """)
        try execute("PRAGMA user_version=3")
    }

    private func captureGeneration() throws -> Int64 {
        let prepared = try statement("SELECT capture_generation FROM archive_meta WHERE id=1", [])
        defer { sqlite3_finalize(prepared) }
        let code = sqlite3_step(prepared)
        guard code == SQLITE_ROW else { throw ArchiveError.unavailable(code == SQLITE_DONE ? SQLITE_CORRUPT : code) }
        let value = sqlite3_column_int64(prepared, 0)
        guard sqlite3_column_type(prepared, 0) == SQLITE_INTEGER, value >= 0 else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
        return value
    }

    private func migrateOutboxReceiptToVersion4() throws {
        try execute("""
            ALTER TABLE archive_meta ADD COLUMN outbox_high_water INTEGER NOT NULL DEFAULT -1
            CHECK(typeof(outbox_high_water)='integer' AND outbox_high_water>=-1)
            """)
        try execute("""
            ALTER TABLE archive_meta ADD COLUMN outbox_last_event_key TEXT NOT NULL DEFAULT ''
            CHECK(typeof(outbox_last_event_key)='text' AND length(CAST(outbox_last_event_key AS BLOB))<=128)
            """)
        try execute("PRAGMA user_version=4")
    }

    private func createMediaTable(maxBytes: Int) throws {
        try execute("""
            CREATE TABLE event_media(event_key TEXT NOT NULL REFERENCES events(event_key) ON DELETE CASCADE,
            slot INTEGER NOT NULL CHECK(slot>=0 AND slot<10), kind TEXT NOT NULL CHECK(length(CAST(kind AS BLOB))<=32), mime TEXT NOT NULL CHECK(length(CAST(mime AS BLOB))<=256),
            payload BLOB NOT NULL CHECK(length(payload)<=\(maxBytes)), byte_cost INTEGER NOT NULL CHECK(byte_cost=length(payload)+length(CAST(kind AS BLOB))+length(CAST(mime AS BLOB))+64),
            PRIMARY KEY(event_key,slot))
            """)
    }

    private func installMediaTriggers() throws {
        try execute("""
            CREATE TRIGGER archive_media_insert_usage AFTER INSERT ON event_media BEGIN
            UPDATE archive_usage SET payload_bytes=payload_bytes+NEW.byte_cost WHERE id=1;
            END
            """)
        try execute("""
            CREATE TRIGGER archive_media_delete_usage AFTER DELETE ON event_media BEGIN
            UPDATE archive_usage SET payload_bytes=payload_bytes-OLD.byte_cost WHERE id=1;
            END
            """)
        // Also clean up on an older open connection whose foreign_keys pragma
        // predates this migration; duplicate cascade deletion is harmless.
        try execute("CREATE TRIGGER archive_event_media_delete AFTER DELETE ON events BEGIN DELETE FROM event_media WHERE event_key=OLD.event_key; END")
        try execute("CREATE TRIGGER archive_media_immutable BEFORE UPDATE ON event_media BEGIN SELECT RAISE(ABORT,'Archive media is immutable'); END")
    }

    private func migrateMediaToVersion5() throws {
        try createMediaTable(maxBytes: 16 * 1024 * 1024)
        try installMediaTriggers()
        try execute("PRAGMA user_version=5")
    }

    private func migrateMediaToVersion7() throws {
        for name in ["archive_media_insert_usage", "archive_media_delete_usage", "archive_event_media_delete", "archive_media_immutable"] {
            try execute("DROP TRIGGER " + name)
        }
        try execute("ALTER TABLE event_media RENAME TO event_media_v6")
        try createMediaTable(maxBytes: 512 * 1024 * 1024)
        // Usage counters already include these rows. Install triggers only after copying.
        try execute("INSERT INTO event_media SELECT * FROM event_media_v6")
        try execute("DROP TABLE event_media_v6")
        try installMediaTriggers()
        try execute("PRAGMA user_version=7")
    }

    private func migrateGalleryToVersion8() throws {
        // An encrypted index of retained attachment slots supports newest-first
        // browsing and type filtering without scanning text-only messages.
        try execute("""
            CREATE TABLE media_gallery(sequence INTEGER NOT NULL REFERENCES events(sequence) ON DELETE CASCADE,
            slot INTEGER NOT NULL CHECK(slot>=0 AND slot<10),event_key TEXT NOT NULL,kind TEXT NOT NULL,
            PRIMARY KEY(sequence,slot),UNIQUE(event_key,slot)) WITHOUT ROWID
            """)
        try execute("CREATE INDEX media_gallery_kind ON media_gallery(kind,sequence,slot)")
        try execute("INSERT INTO media_gallery SELECT e.sequence,m.slot,e.event_key,m.kind FROM events e JOIN event_media m ON m.event_key=e.event_key")
        try execute("""
            CREATE TRIGGER archive_gallery_insert AFTER INSERT ON event_media BEGIN
            INSERT INTO media_gallery SELECT sequence,NEW.slot,event_key,NEW.kind FROM events WHERE event_key=NEW.event_key;
            END
            """)
        try execute("""
            CREATE TRIGGER archive_gallery_delete AFTER DELETE ON event_media BEGIN
            DELETE FROM media_gallery WHERE event_key=OLD.event_key AND slot=OLD.slot;
            END
            """)
        try execute("PRAGMA user_version=8")
    }

    private func migrateSearchToVersion6() throws {
        // FTS5 is already enabled in both vendored SQLCipher build definitions.
        try execute("CREATE VIRTUAL TABLE events_search USING fts5(text, content='events', content_rowid='sequence', tokenize='unicode61 remove_diacritics 2')")
        try execute("""
            CREATE TRIGGER archive_search_insert AFTER INSERT ON events BEGIN
            INSERT INTO events_search(rowid,text) VALUES(NEW.sequence,NEW.text); END
            """)
        try execute("""
            CREATE TRIGGER archive_search_delete AFTER DELETE ON events BEGIN
            INSERT INTO events_search(events_search,rowid,text) VALUES('delete',OLD.sequence,OLD.text); END
            """)
        try execute("INSERT INTO events_search(events_search) VALUES('rebuild')")
        try execute("PRAGMA user_version=6")
    }

    /// Bounded bytes copied from an already completed local file. No network or plaintext temporary file.
    public func recordMedia(eventKey: String, slot: Int, kind: String, mimeType: String, data: Data,
                            generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        submit(completion) {
            guard !eventKey.isEmpty, eventKey.utf8.count <= 128, !eventKey.contains("\0"),
                  self.clock() >= 0, (0..<10).contains(slot), data.count <= 16 * 1024 * 1024,
                  (!data.isEmpty || kind == "document"),
                  kind.utf8.count <= 32, mimeType.utf8.count <= 256,
                  !kind.contains("\0"), !mimeType.contains("\0") else { throw ArchiveError.invalidInput }
            return try self.transaction {
                let current = try self.captureGeneration()
                guard current == generation else { throw ArchiveError.generationMismatch(expected: current, received: generation) }
                guard try self.scalar("SELECT COUNT(*) FROM events WHERE event_key=?", [.text(eventKey)]) == 1 else { return false }
                if try self.scalar("SELECT COUNT(*) FROM event_media WHERE event_key=? AND slot=?", [.text(eventKey), .integer(Int64(slot))]) != 0 {
                    guard try self.scalar("SELECT COUNT(*) FROM event_media WHERE event_key=? AND slot=? AND kind=? AND mime=? AND payload=?", [.text(eventKey), .integer(Int64(slot)), .text(kind), .text(mimeType), .blob(data)]) == 1 else { throw ArchiveError.eventConflict }
                    return true
                }
                let cost = Int64(data.count + kind.utf8.count + mimeType.utf8.count + 64)
                try self.execute("INSERT INTO event_media VALUES(?,?,?,?,?,?)", [.text(eventKey), .integer(Int64(slot)), .text(kind), .text(mimeType), .blob(data), .integer(cost)])
                _ = try self.prune(now: self.clock(), policy: self.retention)
                return try self.scalar("SELECT COUNT(*) FROM event_media WHERE event_key=? AND slot=?", [.text(eventKey), .integer(Int64(slot))]) == 1
            }
        }
    }

    /// Streams a completed local file into encrypted SQLite pages. Only bounded
    /// buffers are allocated. The row is visible only after the whole transaction commits.
    public func recordMediaFile(eventKey: String, slot: Int, kind: String, mimeType: String, source: URL,
                                expectedSize: Int64? = nil, generation: Int64,
                                completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        submit(completion) {
            guard source.isFileURL, !source.path.contains("\0"), !eventKey.isEmpty,
                  eventKey.utf8.count <= 128, !eventKey.contains("\0"), (0..<10).contains(slot),
                  kind.utf8.count <= 32, mimeType.utf8.count <= 256,
                  !kind.contains("\0"), !mimeType.contains("\0"), expectedSize.map({ $0 >= 0 }) ?? true else { throw ArchiveError.invalidInput }
            let now = self.clock()
            guard now >= 0 else { throw ArchiveError.invalidInput }
            let fd = source.path.withCString { Darwin.open($0, O_RDONLY | O_NOFOLLOW | O_NONBLOCK) }
            guard fd >= 0 else {
                if errno == ENOENT || errno == ELOOP || errno == ENOTDIR { return false }
                throw ArchiveError.protectionUnavailable
            }
            defer { Darwin.close(fd) }
            var info = stat()
            guard fstat(fd, &info) == 0 else { throw ArchiveError.protectionUnavailable }
            guard (info.st_mode & S_IFMT) == S_IFREG, info.st_size >= 0, info.st_size <= 512 * 1024 * 1024 else { return false }
            let size = Int64(info.st_size)
            // A zero-byte document can be valid; a zero-byte image/video/voice
            // file contains no usable media, even if it has a completed-path name.
            guard size > 0 || kind == "document" else { return false }
            guard expectedSize.map({ $0 == size }) ?? true else { return false }
            return try self.transaction {
                let current = try self.captureGeneration()
                guard current == generation else { throw ArchiveError.generationMismatch(expected: current, received: generation) }
                guard try self.scalar("SELECT COUNT(*) FROM events WHERE event_key=?", [.text(eventKey)]) == 1 else { return false }
                if let existing = try self.mediaRow(eventKey: eventKey, slot: slot) {
                    guard existing.size == size, existing.kind == kind, existing.mime == mimeType else { throw ArchiveError.eventConflict }
                    if size > 0 {
                        try self.withMediaBlob(rowId: existing.id, writable: false) { blob in
                            var buffer = [UInt8](repeating: 0, count: 64 * 1024)
                            try Self.streamFile(fd: fd, before: info) { source, count, offset in
                                let code = buffer.withUnsafeMutableBytes { sqlite3_blob_read(blob, $0.baseAddress, Int32(count), Int32(offset)) }
                                guard code == SQLITE_OK else { throw ArchiveError.unavailable(code) }
                                let equal = buffer.withUnsafeBytes { memcmp($0.baseAddress!, source, count) == 0 }
                                guard equal else { throw ArchiveError.eventConflict }
                            }
                        }
                    } else {
                        try Self.streamFile(fd: fd, before: info) { _, _, _ in }
                    }
                    return true
                }
                let cost = size + Int64(kind.utf8.count + mimeType.utf8.count + 64)
                // The complete snapshot must fit, including its text and earlier
                // attachments. Otherwise adding this file would evict its own
                // parent and discard content that was already safely preserved.
                let ownCost = try self.scalar("SELECT payload_bytes + COALESCE((SELECT SUM(byte_cost) FROM event_media WHERE event_key=?),0) FROM events WHERE event_key=?", [.text(eventKey), .text(eventKey)])
                guard ownCost >= 0 else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
                guard ownCost <= self.retention.maxPayloadBytes, cost <= self.retention.maxPayloadBytes - ownCost else { return false }
                try self.execute("INSERT INTO event_media VALUES(?,?,?,?,zeroblob(?),?)", [.text(eventKey), .integer(Int64(slot)), .text(kind), .text(mimeType), .integer(size), .integer(cost)])
                let rowId = sqlite3_last_insert_rowid(self.handle)
                if size > 0 {
                    try self.withMediaBlob(rowId: rowId, writable: true) { blob in
                        try Self.streamFile(fd: fd, before: info) { bytes, count, offset in
                            let code = sqlite3_blob_write(blob, bytes, Int32(count), Int32(offset))
                            guard code == SQLITE_OK else { throw ArchiveError.unavailable(code) }
                        }
                    }
                } else {
                    try Self.streamFile(fd: fd, before: info) { _, _, _ in }
                }
                _ = try self.prune(now: now, policy: self.retention)
                return try self.scalar("SELECT COUNT(*) FROM event_media WHERE event_key=? AND slot=?", [.text(eventKey), .integer(Int64(slot))]) == 1
            }
        }
    }

    public func mediaInfo(eventKey: String, slot: Int, completion: @escaping (Result<ArchivedMediaInfo?, ArchiveError>) -> Void) {
        submit(completion) {
            try Self.validateMediaLookup(eventKey, slot)
            let now = self.clock()
            guard now >= 0 else { throw ArchiveError.invalidInput }
            _ = try self.transaction { try self.prune(now: now, policy: self.retention) }
            guard let row = try self.mediaRow(eventKey: eventKey, slot: slot) else { return nil }
            return ArchivedMediaInfo(slot: slot, kind: row.kind, mimeType: row.mime, byteCount: row.size)
        }
    }

    /// Random access for local players/exporters, capped at 1 MiB per read.
    public func mediaRange(eventKey: String, slot: Int, offset: Int64, count: Int,
                           completion: @escaping (Result<Data?, ArchiveError>) -> Void) {
        submit(completion) {
            try Self.validateMediaLookup(eventKey, slot)
            let now = self.clock()
            guard now >= 0, offset >= 0, (0...1024 * 1024).contains(count) else { throw ArchiveError.invalidInput }
            _ = try self.transaction { try self.prune(now: now, policy: self.retention) }
            guard let row = try self.mediaRow(eventKey: eventKey, slot: slot) else { return nil }
            guard offset <= row.size else { throw ArchiveError.invalidInput }
            let length = min(count, Int(row.size - offset))
            if length == 0 { return Data() }
            return try self.withMediaBlob(rowId: row.id, writable: false) { blob in
                var data = Data(count: length)
                let code = data.withUnsafeMutableBytes { sqlite3_blob_read(blob, $0.baseAddress, Int32(length), Int32(offset)) }
                guard code == SQLITE_OK else { throw ArchiveError.unavailable(code) }
                return data
            }
        }
    }

    private static func validateMediaLookup(_ key: String, _ slot: Int) throws {
        guard !key.isEmpty, key.utf8.count <= 128, !key.contains("\0"), (0..<10).contains(slot) else { throw ArchiveError.invalidInput }
    }

    private func mediaRow(eventKey: String, slot: Int) throws -> (id: Int64, size: Int64, kind: String, mime: String)? {
        let statement = try self.statement("SELECT rowid,length(payload),kind,mime FROM event_media WHERE event_key=? AND slot=?", [.text(eventKey), .integer(Int64(slot))])
        defer { sqlite3_finalize(statement) }
        let code = sqlite3_step(statement)
        if code == SQLITE_DONE { return nil }
        guard code == SQLITE_ROW else { throw ArchiveError.unavailable(code) }
        let size = sqlite3_column_int64(statement, 1)
        guard size >= 0, size <= 512 * 1024 * 1024 else { throw ArchiveError.invalidInput }
        return (sqlite3_column_int64(statement, 0), size, try self.text(statement, 2), try self.text(statement, 3))
    }

    private func withMediaBlob<Value>(rowId: Int64, writable: Bool, _ body: (OpaquePointer) throws -> Value) throws -> Value {
        var pointer: OpaquePointer?
        let code = sqlite3_blob_open(self.handle, "main", "event_media", "payload", rowId, writable ? 1 : 0, &pointer)
        guard code == SQLITE_OK, let blob = pointer else { throw ArchiveError.unavailable(code) }
        var needsClose = true
        defer { if needsClose { sqlite3_blob_close(blob) } }
        let result = try body(blob)
        let closeCode = sqlite3_blob_close(blob)
        needsClose = false
        guard closeCode == SQLITE_OK else { throw ArchiveError.unavailable(closeCode) }
        return result
    }

    private static func streamFile(fd: Int32, before: stat, _ consume: (UnsafeRawPointer, Int, Int64) throws -> Void) throws {
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        var offset: Int64 = 0
        while offset < before.st_size {
            let length = min(buffer.count, Int(before.st_size - offset))
            let count = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, length) }
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { throw ArchiveError.protectionUnavailable }
            try buffer.withUnsafeBytes { try consume($0.baseAddress!, count, offset) }
            offset += Int64(count)
        }
        var after = stat()
        guard fstat(fd, &after) == 0, before.st_size == after.st_size,
              before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
              before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec else { throw ArchiveError.protectionUnavailable }
    }

    public func containsMedia(eventKey: String, slot: Int, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        submit(completion) {
            guard (0..<10).contains(slot), eventKey.utf8.count <= 128 else { throw ArchiveError.invalidInput }
            return try self.scalar("SELECT COUNT(*) FROM event_media WHERE event_key=? AND slot=?", [.text(eventKey), .integer(Int64(slot))]) == 1
        }
    }

    public func media(eventKey: String, slot: Int, completion: @escaping (Result<ArchivedMedia?, ArchiveError>) -> Void) {
        submit(completion) {
            let now = self.clock()
            guard now >= 0, (0..<10).contains(slot), !eventKey.isEmpty, eventKey.utf8.count <= 128, !eventKey.contains("\0") else { throw ArchiveError.invalidInput }
            // A direct preview can outlive the page that opened it. Enforce age
            // retention even when no writes or list refresh have happened.
            _ = try self.transaction { try self.prune(now: now, policy: self.retention) }
            guard let info = try self.mediaRow(eventKey: eventKey, slot: slot) else { return nil }
            guard info.size <= 16 * 1024 * 1024 else { throw ArchiveError.invalidInput }
            let row = try self.statement("SELECT kind,mime,payload FROM event_media WHERE event_key=? AND slot=?", [.text(eventKey), .integer(Int64(slot))])
            defer { sqlite3_finalize(row) }
            let code = sqlite3_step(row)
            if code == SQLITE_DONE { return nil }
            guard code == SQLITE_ROW else { throw ArchiveError.unavailable(code) }
            let count = Int(sqlite3_column_bytes(row, 2))
            guard count <= 16 * 1024 * 1024 else { throw ArchiveError.invalidInput }
            let bytes = sqlite3_column_blob(row, 2).map { Data(bytes: $0, count: count) } ?? Data()
            return ArchivedMedia(slot: slot, kind: try self.text(row, 0), mimeType: try self.text(row, 1), data: bytes)
        }
    }

    private func outboxReceipt() throws -> (index: Int64, eventKey: String) {
        let prepared = try statement("SELECT outbox_high_water,outbox_last_event_key FROM archive_meta WHERE id=1", [])
        defer { sqlite3_finalize(prepared) }
        let code = sqlite3_step(prepared)
        guard code == SQLITE_ROW else { throw ArchiveError.unavailable(code == SQLITE_DONE ? SQLITE_CORRUPT : code) }
        let index = sqlite3_column_int64(prepared, 0)
        let key = try text(prepared, 1)
        guard sqlite3_column_type(prepared, 0) == SQLITE_INTEGER,
              sqlite3_column_type(prepared, 1) == SQLITE_TEXT, index >= -1,
              key.utf8.count <= 128, !key.contains("\0"),
              (index == -1 && key.isEmpty) || (index >= 0 && !key.isEmpty) else {
            throw ArchiveError.unavailable(SQLITE_CORRUPT)
        }
        return (index, key)
    }

    private func submit<T>(_ completion: @escaping (Result<T, ArchiveError>) -> Void, operation: @escaping () throws -> T) {
        worker.async {
            let result: Result<T, ArchiveError>
            do {
                guard self.handle != nil else { throw ArchiveError.closed }
                result = .success(try operation())
            } catch { result = .failure(Self.normalized(error)) }
            self.callbackQueue.async { completion(result) }
        }
    }

    private func transaction<T>(_ operation: () throws -> T) throws -> T {
        try execute("BEGIN IMMEDIATE")
        do { let result = try operation(); try execute("COMMIT"); return result }
        catch { try? execute("ROLLBACK"); throw error }
    }

    private func statement(_ sql: String, _ values: [SQLValue]) throws -> OpaquePointer {
        guard let handle = handle else { throw ArchiveError.closed }
        var pointer: OpaquePointer?
        let code = sqlite3_prepare_v2(handle, sql, -1, &pointer, nil)
        guard code == SQLITE_OK, let pointer = pointer else { throw ArchiveError.unavailable(code) }
        do {
            for (offset, value) in values.enumerated() {
                let index = Int32(offset + 1)
                let result: Int32
                switch value {
                case let .integer(number): result = sqlite3_bind_int64(pointer, index, number)
                case let .optionalInteger(number): result = number.map { sqlite3_bind_int64(pointer, index, $0) } ?? sqlite3_bind_null(pointer, index)
                case let .text(text): result = text.withCString { sqlite3_bind_text(pointer, index, $0, Int32(text.utf8.count), archiveTransient) }
                case let .blob(data):
                    result = data.isEmpty ? sqlite3_bind_zeroblob(pointer, index, 0) : data.withUnsafeBytes { sqlite3_bind_blob(pointer, index, $0.baseAddress, Int32(data.count), archiveTransient) }
                }
                guard result == SQLITE_OK else { throw ArchiveError.unavailable(result) }
            }
            return pointer
        } catch { sqlite3_finalize(pointer); throw error }
    }

    private func execute(_ sql: String, _ values: [SQLValue] = []) throws {
        let prepared = try statement(sql, values); defer { sqlite3_finalize(prepared) }
        var code = sqlite3_step(prepared)
        while code == SQLITE_ROW { code = sqlite3_step(prepared) }
        guard code == SQLITE_DONE else { throw ArchiveError.unavailable(code) }
    }

    private func scalar(_ sql: String, _ values: [SQLValue] = []) throws -> Int64 {
        let prepared = try statement(sql, values); defer { sqlite3_finalize(prepared) }
        let code = sqlite3_step(prepared)
        guard code == SQLITE_ROW else { throw ArchiveError.unavailable(code) }
        return sqlite3_column_int64(prepared, 0)
    }

    private func textScalar(_ sql: String) throws -> String {
        let prepared = try statement(sql, []); defer { sqlite3_finalize(prepared) }
        let code = sqlite3_step(prepared)
        guard code == SQLITE_ROW else { throw ArchiveError.unavailable(code) }
        return try text(prepared, 0)
    }

    private func text(_ pointer: OpaquePointer, _ column: Int32) throws -> String {
        let length = Int(sqlite3_column_bytes(pointer, column))
        guard length <= 256 * 1024 else { throw ArchiveError.invalidInput }
        guard let bytes = sqlite3_column_text(pointer, column) else { return "" }
        guard let value = String(data: Data(bytes: bytes, count: length), encoding: .utf8) else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
        return value
    }

    private func read(_ clause: String, _ values: [SQLValue]) throws -> [ArchivedRecord] {
        let prepared = try statement("SELECT sequence,event_key,account_id,peer_id,namespace,message_id,kind,observed_at,server_edit_at,text,author_id,sent_at,entities FROM events " + clause, values)
        defer { sqlite3_finalize(prepared) }
        var result: [ArchivedRecord] = []
        var code = sqlite3_step(prepared)
        while code == SQLITE_ROW {
            guard result.count < 200, let kind = ArchiveEventKind(rawValue: sqlite3_column_int(prepared, 6)) else { throw ArchiveError.invalidInput }
            let count = Int(sqlite3_column_bytes(prepared, 12))
            guard count <= 64 * 1024 else { throw ArchiveError.invalidInput }
            let entities = sqlite3_column_blob(prepared, 12).map { Data(bytes: $0, count: count) } ?? Data()
            func optional(_ column: Int32) -> Int64? { sqlite3_column_type(prepared, column) == SQLITE_NULL ? nil : sqlite3_column_int64(prepared, column) }
            let message = ArchiveMessageID(accountId: sqlite3_column_int64(prepared, 2), peerId: sqlite3_column_int64(prepared, 3), namespace: sqlite3_column_int(prepared, 4), messageId: sqlite3_column_int(prepared, 5))
            guard message.accountId == accountId else { throw ArchiveError.accountMismatch }
            let event = ArchiveEvent(eventKey: try text(prepared, 1), message: message, kind: kind,
                                     observedAt: sqlite3_column_int64(prepared, 7), serverEditAt: optional(8),
                                     content: ArchiveContent(text: try text(prepared, 9), authorId: optional(10), sentAt: sqlite3_column_int64(prepared, 11), entities: entities))
            let mediaCount = try scalar("SELECT COUNT(*) FROM event_media WHERE event_key=?", [.text(event.eventKey)])
            result.append(ArchivedRecord(sequence: sqlite3_column_int64(prepared, 0), event: event, storedMediaCount: Int(mediaCount)))
            code = sqlite3_step(prepared)
        }
        guard code == SQLITE_DONE else { throw ArchiveError.unavailable(code) }
        return result
    }

    private func prune(now: Int64, policy: ArchiveRetentionPolicy) throws -> Int {
        let before = try currentUsage().records
        let cutoff = max(0, now - min(now, policy.maxAgeSeconds))
        var removed: Int32
        repeat {
            // The retention index bounds each selection; the transaction remains atomic.
            try execute("""
                DELETE FROM events WHERE sequence IN (SELECT sequence FROM events
                WHERE observed_at<? ORDER BY observed_at,sequence LIMIT 256)
                """, [.integer(cutoff)])
            removed = sqlite3_changes(handle)
        } while removed == 256

        var usage = try currentUsage()
        while usage.logicalPayloadBytes > policy.maxPayloadBytes {
            let excess = usage.logicalPayloadBytes - policy.maxPayloadBytes
            let oldest = try oldestPayloads()
            guard !oldest.isEmpty else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
            var bytes: Int64 = 0
            var values: [SQLValue] = []
            for row in oldest {
                bytes += row.bytes
                values.append(.integer(row.sequence))
                if bytes >= excess { break }
            }
            // Only bounded placeholder count is constructed; all sequence values are bound.
            let placeholders = Array(repeating: "?", count: values.count).joined(separator: ",")
            try execute("DELETE FROM events WHERE sequence IN (\(placeholders))", values)
            let updated = try currentUsage()
            guard updated.records < usage.records else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
            usage = updated
        }
        return Int(before - usage.records)
    }

    private func oldestPayloads() throws -> [(sequence: Int64, bytes: Int64)] {
        let prepared = try statement("SELECT sequence,payload_bytes+(SELECT COALESCE(SUM(byte_cost),0) FROM event_media WHERE event_key=events.event_key) FROM events ORDER BY observed_at,sequence LIMIT 256", [])
        defer { sqlite3_finalize(prepared) }
        var result: [(sequence: Int64, bytes: Int64)] = []
        var code = sqlite3_step(prepared)
        while code == SQLITE_ROW {
            let sequence = sqlite3_column_int64(prepared, 0), bytes = sqlite3_column_int64(prepared, 1)
            guard result.count < 256, sequence > 0, sqlite3_column_type(prepared, 1) == SQLITE_INTEGER,
                  bytes >= 0 else { throw ArchiveError.unavailable(SQLITE_CORRUPT) }
            result.append((sequence, bytes))
            code = sqlite3_step(prepared)
        }
        guard code == SQLITE_DONE else { throw ArchiveError.unavailable(code) }
        return result
    }

    private func validate(_ event: ArchiveEvent, now: Int64) throws {
        guard event.message.accountId == accountId else { throw ArchiveError.accountMismatch }
        guard !event.eventKey.isEmpty, event.eventKey.utf8.count <= 128, !event.eventKey.contains("\0"),
              event.message.messageId > 0, event.observedAt >= 0, event.observedAt <= now,
              event.content.sentAt >= 0, event.serverEditAt.map({ $0 >= 0 }) ?? true,
              event.content.text.utf8.count <= 256 * 1024, event.content.entities.count <= 64 * 1024 else { throw ArchiveError.invalidInput }
    }

    private static func validatePage(_ before: Int64?, _ limit: Int) throws {
        guard (1...200).contains(limit), before.map({ $0 > 0 }) ?? true else { throw ArchiveError.invalidInput }
    }
    private static func validateRetention(_ policy: ArchiveRetentionPolicy) throws {
        guard policy.maxAgeSeconds > 0, policy.maxPayloadBytes > 0, policy.maxPayloadBytes <= 1024 * 1024 * 1024 else { throw ArchiveError.invalidInput }
    }
    private static func normalized(_ error: Error) -> ArchiveError { (error as? ArchiveError) ?? .unavailable(SQLITE_ERROR) }

    private static func physicalPath(_ url: URL) throws -> String {
        let resolved = url.withUnsafeFileSystemRepresentation { path -> UnsafeMutablePointer<CChar>? in
            guard let path = path else { return nil }
            return realpath(path, nil)
        }
        guard let resolved = resolved else { throw ArchiveError.protectionUnavailable }
        defer { free(resolved) }
        guard let value = String(validatingUTF8: resolved) else { throw ArchiveError.protectionUnavailable }
        return value
    }

    /// lstat sees dangling links; fileExists follows them and misses unsafe endpoints.
    private static func entryExists(_ url: URL) throws -> Bool {
        var info = stat()
        let result = url.withUnsafeFileSystemRepresentation { path -> Int32 in
            guard let path = path else { return -1 }
            return lstat(path, &info)
        }
        if result == 0 { return true }
        if errno == ENOENT { return false }
        throw ArchiveError.protectionUnavailable
    }

    private static func protectedDirectory(_ url: URL) throws {
        do {
            if try entryExists(url) {
                guard try FileManager.default.attributesOfItem(atPath: url.path)[.type] as? FileAttributeType == .typeDirectory else { throw ArchiveError.protectionUnavailable }
            } else {
                try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            }
            try protect(url, permissions: 0o700)
        } catch { throw ArchiveError.protectionUnavailable }
    }
    private static func protectFile(_ url: URL) throws {
        do {
            guard try FileManager.default.attributesOfItem(atPath: url.path)[.type] as? FileAttributeType == .typeRegular else { throw ArchiveError.protectionUnavailable }
            try protect(url, permissions: 0o600)
        } catch { throw ArchiveError.protectionUnavailable }
    }
    private static func protect(_ url: URL, permissions: Int) throws {
        var attributes: [FileAttributeKey: Any] = [.posixPermissions: permissions]
        #if os(iOS)
        attributes[.protectionKey] = FileProtectionType.complete
        #endif
        try FileManager.default.setAttributes(attributes, ofItemAtPath: url.path)
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        var protectedURL = url; try protectedURL.setResourceValues(values)
    }
}
