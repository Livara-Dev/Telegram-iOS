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
                    try store.openDatabase(key: key)
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

    /// Commit all eligible events atomically. An event-key collision with different content rolls back the batch.
    public func record(_ captures: [ArchiveCapture], policy: ArchiveCapturePolicy,
                       completion: @escaping (Result<ArchiveWriteResult, ArchiveError>) -> Void) {
        submit(completion) {
            let now = self.clock()
            guard now >= 0, captures.count <= 256 else { throw ArchiveError.invalidInput }
            var bytes: Int64 = 0
            for capture in captures {
                try self.validate(capture.event, now: now)
                bytes += capture.event.logicalBytes
            }
            guard bytes <= 8 * 1024 * 1024 else { throw ArchiveError.invalidInput }
            return try self.transaction {
                var inserted = 0, duplicates = 0, excluded = 0
                for capture in captures {
                    let event = capture.event
                    guard policy.allows(event, context: capture.context) else { excluded += 1; continue }
                    if let existing = try self.read("WHERE event_key = ?", [.text(event.eventKey)]).first {
                        guard existing.event == event else { throw ArchiveError.eventConflict }
                        duplicates += 1
                    } else {
                        try self.execute("""
                            INSERT INTO events(event_key,account_id,peer_id,namespace,message_id,kind,observed_at,
                            server_edit_at,text,author_id,sent_at,entities,payload_bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """, [.text(event.eventKey), .integer(self.accountId), .integer(event.message.peerId),
                                   .integer(Int64(event.message.namespace)), .integer(Int64(event.message.messageId)),
                                   .integer(Int64(event.kind.rawValue)), .integer(event.observedAt), .optionalInteger(event.serverEditAt),
                                   .text(event.content.text), .optionalInteger(event.content.authorId), .integer(event.content.sentAt),
                                   .blob(event.content.entities), .integer(event.logicalBytes)])
                        inserted += 1
                    }
                }
                let removed = try self.prune(now: now, policy: self.retention)
                return ArchiveWriteResult(inserted: inserted, duplicates: duplicates, excluded: excluded, removedByRetention: removed)
            }
        }
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

    /// Removes this account's events. Does not delete Telegram cache/session data or the account's key.
    public func purge(completion: @escaping (Result<Void, ArchiveError>) -> Void) {
        submit(completion) { try self.transaction { try self.execute("DELETE FROM events") } }
    }

    private func openDatabase(key: Data) throws {
        var opened: OpaquePointer?
        let code = sqlite3_open_v2(databasePath, &opened, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX | SQLITE_OPEN_NOFOLLOW, nil)
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
        let version = try scalar("PRAGMA user_version")
        guard (0...2).contains(version) else { throw ArchiveError.unsupportedSchema(Int(version)) }
        try transaction {
            // Another connection may have migrated while this one waited for the writer lock.
            let lockedVersion = try scalar("PRAGMA user_version")
            guard (0...2).contains(lockedVersion) else { throw ArchiveError.unsupportedSchema(Int(lockedVersion)) }
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
            _ = try currentUsage()
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
            result.append(ArchivedRecord(sequence: sqlite3_column_int64(prepared, 0), event: event))
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
        let prepared = try statement("SELECT sequence,payload_bytes FROM events ORDER BY observed_at,sequence LIMIT 256", [])
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
