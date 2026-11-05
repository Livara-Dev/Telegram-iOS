// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Darwin
import sqlcipher

/// All calls run on ArchiveStore's serial worker. Source SQL/schema text is never
/// executed on the destination; only bounded values enter fixed prepared SQL.
enum ArchiveTransferDatabase {
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    private static let maximumVersions: Int64 = 2_000_000

    static func installCancellation(_ database: OpaquePointer, _ cancellation: ArchiveTransferCancellation) {
        sqlite3_progress_handler(database, 1000, { pointer in
            guard let pointer else { return 1 }
            return Unmanaged<ArchiveTransferCancellation>.fromOpaque(pointer).takeUnretainedValue().isCancelled ? 1 : 0
        }, Unmanaged.passUnretained(cancellation).toOpaque())
    }

    static func clearCancellation(_ database: OpaquePointer) {
        sqlite3_progress_handler(database, 0, nil, nil)
    }

    static func openSnapshot(_ url: URL, key: Data, cancellation: ArchiveTransferCancellation) throws -> OpaquePointer {
        let file = try ArchiveTransferFiles.openRead(url)
        Darwin.close(file.fd)
        let physical = try ArchiveTransferFiles.physicalURL(url)
        var database: OpaquePointer?
        let code = sqlite3_open_v2(physical.path, &database, SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX | SQLITE_OPEN_NOFOLLOW, nil)
        guard code == SQLITE_OK, let database else {
            if let database { sqlite3_close_v2(database) }
            throw ArchiveTransferError.invalidArchive
        }
        do {
            sqlite3_busy_timeout(database, 1000)
            sqlite3_limit(database, SQLITE_LIMIT_SQL_LENGTH, 64 * 1024)
            sqlite3_limit(database, SQLITE_LIMIT_LENGTH, 513 * 1024 * 1024)
            sqlite3_limit(database, SQLITE_LIMIT_COLUMN, 128)
            sqlite3_limit(database, SQLITE_LIMIT_EXPR_DEPTH, 32)
            sqlite3_limit(database, SQLITE_LIMIT_COMPOUND_SELECT, 16)
            sqlite3_limit(database, SQLITE_LIMIT_ATTACHED, 0)
            self.installCancellation(database, cancellation)
            try self.setKey(database, key)
            try self.execute(database, "PRAGMA temp_store=MEMORY")
            try self.execute(database, "PRAGMA query_only=ON")
            try self.execute(database, "PRAGMA trusted_schema=OFF")
            guard try self.scalar(database, "PRAGMA user_version") == 8 else { throw ArchiveTransferError.unsupportedVersion }
            return database
        } catch {
            sqlite3_close_v2(database)
            if cancellation.isCancelled { throw ArchiveTransferError.cancelled }
            throw error
        }
    }

    private static func setKey(_ database: OpaquePointer, _ key: Data) throws {
        guard key.count == 32 else { throw ArchiveTransferError.invalidArchive }
        // Fixed-format key hex only. Statements/errors are never logged.
        let hex = key.map { String(format: "%02x", $0) }.joined()
        try self.execute(database, "PRAGMA key=\"x'\(hex)'\"")
        try self.execute(database, "PRAGMA cipher_plaintext_header_size=0")
        guard !(try self.textScalar(database, "PRAGMA cipher_version")).isEmpty else {
            throw ArchiveTransferError.invalidArchive
        }
    }

    static func validateBasic(source: OpaquePointer, accountId: Int64,
                              cancellation: ArchiveTransferCancellation) throws {
        try cancellation.check()
        let profile = try self.schema(source)
        guard profile["events"]?.hasPrefix("table|events|") == true,
              profile["event_media"]?.hasPrefix("table|event_media|") == true,
              profile["archive_meta"]?.hasPrefix("table|archive_meta|") == true else {
            throw ArchiveTransferError.invalidArchive
        }
        guard try self.scalar(source, "SELECT account_id FROM archive_meta WHERE id=1") == accountId,
              try self.scalar(source, "SELECT COUNT(*) FROM archive_meta") == 1,
              try self.scalar(source, "SELECT COUNT(*) FROM events WHERE account_id!=\(accountId)") == 0 else {
            throw ArchiveTransferError.wrongAccount
        }
        guard try self.scalar(source, "SELECT COUNT(*) FROM events") <= self.maximumVersions,
              try self.scalar(source, "SELECT COUNT(*) FROM event_media m LEFT JOIN events e ON e.event_key=m.event_key WHERE e.event_key IS NULL") == 0 else {
            throw ArchiveTransferError.invalidArchive
        }
        // octet_length reads the cell's length metadata, avoiding materializing
        // an attacker-sized text/entity value merely to reject its size later.
        guard try self.scalar(source, """
            SELECT COUNT(*) FROM events WHERE
            typeof(event_key)!='text' OR octet_length(event_key)>128 OR
            typeof(text)!='text' OR octet_length(text)>262144 OR
            typeof(entities)!='blob' OR octet_length(entities)>65536
            """) == 0,
              try self.scalar(source, """
            SELECT COUNT(*) FROM event_media WHERE
            typeof(event_key)!='text' OR octet_length(event_key)>128 OR
            typeof(slot)!='integer' OR slot<0 OR slot>=10 OR
            typeof(kind)!='text' OR octet_length(kind)>32 OR
            typeof(mime)!='text' OR octet_length(mime)>256 OR
            typeof(payload)!='blob' OR octet_length(payload)>536870912 OR
            typeof(byte_cost)!='integer' OR byte_cost!=octet_length(payload)+octet_length(kind)+octet_length(mime)+64
            """) == 0 else { throw ArchiveTransferError.invalidArchive }
        // Run structural checks only after bounded-value validation. This
        // avoids CHECK expressions materializing oversized attacker input.
        guard try self.textScalar(source, "PRAGMA quick_check") == "ok" else { throw ArchiveTransferError.invalidArchive }
        let rows = try self.prepare(source, "SELECT event_key,account_id,peer_id,namespace,message_id,kind,observed_at,server_edit_at,text,author_id,sent_at,entities,payload_bytes FROM events ORDER BY sequence")
        defer { sqlite3_finalize(rows) }
        var code = sqlite3_step(rows)
        while code == SQLITE_ROW {
            try cancellation.check()
            let event = try self.event(rows, accountId: accountId, now: Int64(Date().timeIntervalSince1970))
            guard sqlite3_column_type(rows, 12) == SQLITE_INTEGER,
                  sqlite3_column_int64(rows, 12) == event.logicalBytes else { throw ArchiveTransferError.invalidArchive }
            code = sqlite3_step(rows)
        }
        guard code == SQLITE_DONE else {
            if cancellation.isCancelled { throw ArchiveTransferError.cancelled }
            throw ArchiveTransferError.invalidArchive
        }
    }

    static func validate(source: OpaquePointer, destination: OpaquePointer, accountId: Int64,
                         cancellation: ArchiveTransferCancellation) throws {
        try cancellation.check()
        guard try self.schema(source) == self.schema(destination) else { throw ArchiveTransferError.unsupportedVersion }
        guard try self.scalar(source, "SELECT account_id FROM archive_meta WHERE id=1") == accountId else {
            throw ArchiveTransferError.wrongAccount
        }
    }

    /// Caller owns a destination transaction and commits only after retention
    /// and a final cancellation/generation check. Destination replay receipts and
    /// purge generations are deliberately not imported from the snapshot.
    static func merge(source: OpaquePointer, destination: OpaquePointer, sourceAccountId: Int64,
                      accountId: Int64, now: Int64,
                      cancellation: ArchiveTransferCancellation) throws -> (added: Int, duplicates: Int, media: Int) {
        var added = 0, duplicates = 0, media = 0
        let events = try self.prepare(source, "SELECT event_key,account_id,peer_id,namespace,message_id,kind,observed_at,server_edit_at,text,author_id,sent_at,entities FROM events ORDER BY sequence")
        defer { sqlite3_finalize(events) }
        var code = sqlite3_step(events)
        while code == SQLITE_ROW {
            try cancellation.check()
            guard Int64(added + duplicates) < self.maximumVersions else { throw ArchiveTransferError.invalidArchive }
            let sourceEvent = try self.event(events, accountId: sourceAccountId, now: now)
            // Only the archive namespace is mapped to this installation's
            // record. Stable user ownership was authenticated by the envelope.
            let event = ArchiveEvent(eventKey: sourceEvent.eventKey,
                message: ArchiveMessageID(accountId: accountId, peerId: sourceEvent.message.peerId,
                                          namespace: sourceEvent.message.namespace, messageId: sourceEvent.message.messageId),
                kind: sourceEvent.kind, observedAt: sourceEvent.observedAt, serverEditAt: sourceEvent.serverEditAt,
                content: sourceEvent.content)
            let existing = try self.prepare(destination, "SELECT event_key,account_id,peer_id,namespace,message_id,kind,observed_at,server_edit_at,text,author_id,sent_at,entities FROM events WHERE event_key=?")
            defer { sqlite3_finalize(existing) }
            try self.bind(existing, text: event.eventKey, index: 1)
            let found = sqlite3_step(existing)
            if found == SQLITE_ROW {
                guard try self.event(existing, accountId: accountId, now: now) == event else { throw ArchiveTransferError.conflict }
                duplicates += 1
            } else if found == SQLITE_DONE {
                try self.insert(event, destination: destination); added += 1
            } else { throw ArchiveTransferError.storageUnavailable }
            media += try self.mergeMedia(source: source, destination: destination, eventKey: event.eventKey, cancellation: cancellation)
            try cancellation.checkpoint(.snapshotRow)
            code = sqlite3_step(events)
        }
        guard code == SQLITE_DONE else {
            if cancellation.isCancelled { throw ArchiveTransferError.cancelled }
            throw ArchiveTransferError.invalidArchive
        }
        return (added, duplicates, media)
    }

    private static func event(_ row: OpaquePointer, accountId: Int64, now: Int64) throws -> ArchiveEvent {
        let key = try self.text(row, 0, limit: 128)
        guard !key.isEmpty, !key.contains("\0"), sqlite3_column_int64(row, 1) == accountId,
              let namespace = Int32(exactly: sqlite3_column_int64(row, 3)),
              let messageId = Int32(exactly: sqlite3_column_int64(row, 4)), messageId > 0,
              let rawKind = Int32(exactly: sqlite3_column_int64(row, 5)),
              let kind = ArchiveEventKind(rawValue: rawKind) else { throw ArchiveTransferError.invalidArchive }
        for index in [Int32(1), 2, 3, 4, 5, 6, 10] {
            guard sqlite3_column_type(row, index) == SQLITE_INTEGER else { throw ArchiveTransferError.invalidArchive }
        }
        let observed = sqlite3_column_int64(row, 6), sent = sqlite3_column_int64(row, 10)
        guard observed >= 0, observed <= now, sent >= 0 else { throw ArchiveTransferError.invalidArchive }
        let edit = try self.optionalInteger(row, 7), author = try self.optionalInteger(row, 9)
        guard edit.map({ $0 >= 0 }) ?? true else { throw ArchiveTransferError.invalidArchive }
        return ArchiveEvent(eventKey: key,
            message: ArchiveMessageID(accountId: accountId, peerId: sqlite3_column_int64(row, 2), namespace: namespace, messageId: messageId),
            kind: kind, observedAt: observed, serverEditAt: edit,
            content: ArchiveContent(text: try self.text(row, 8, limit: 256 * 1024), authorId: author, sentAt: sent,
                                    entities: try self.blob(row, 11, limit: 64 * 1024)))
    }

    private static func insert(_ event: ArchiveEvent, destination: OpaquePointer) throws {
        let row = try self.prepare(destination, "INSERT INTO events(event_key,account_id,peer_id,namespace,message_id,kind,observed_at,server_edit_at,text,author_id,sent_at,entities,payload_bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)")
        defer { sqlite3_finalize(row) }
        try self.bind(row, text: event.eventKey, index: 1)
        let integers: [(Int32, Int64)] = [(2, event.message.accountId), (3, event.message.peerId), (4, Int64(event.message.namespace)), (5, Int64(event.message.messageId)), (6, Int64(event.kind.rawValue)), (7, event.observedAt), (11, event.content.sentAt), (13, event.logicalBytes)]
        for (index, value) in integers {
            guard sqlite3_bind_int64(row, index, value) == SQLITE_OK else { throw ArchiveTransferError.storageUnavailable }
        }
        try self.bind(row, optional: event.serverEditAt, index: 8)
        try self.bind(row, text: event.content.text, index: 9)
        try self.bind(row, optional: event.content.authorId, index: 10)
        let code: Int32
        if event.content.entities.isEmpty { code = sqlite3_bind_zeroblob(row, 12, 0) }
        else { code = event.content.entities.withUnsafeBytes { sqlite3_bind_blob(row, 12, $0.baseAddress, Int32($0.count), self.transient) } }
        guard code == SQLITE_OK, sqlite3_step(row) == SQLITE_DONE else { throw ArchiveTransferError.storageUnavailable }
    }

    private static func mergeMedia(source: OpaquePointer, destination: OpaquePointer, eventKey: String,
                                   cancellation: ArchiveTransferCancellation) throws -> Int {
        let row = try self.prepare(source, "SELECT rowid,slot,kind,mime,length(payload),byte_cost FROM event_media WHERE event_key=? ORDER BY slot")
        defer { sqlite3_finalize(row) }
        try self.bind(row, text: eventKey, index: 1)
        var result = 0, count = 0, code = sqlite3_step(row)
        while code == SQLITE_ROW {
            try cancellation.check(); count += 1
            let sourceId = sqlite3_column_int64(row, 0), slot = sqlite3_column_int64(row, 1)
            let size = sqlite3_column_int64(row, 4)
            let kind = try self.text(row, 2, limit: 32), mime = try self.text(row, 3, limit: 256)
            guard count <= 10, (0..<10).contains(slot), (0...Int64(512 * 1024 * 1024)).contains(size),
                  sqlite3_column_int64(row, 5) == size + Int64(kind.utf8.count + mime.utf8.count + 64) else { throw ArchiveTransferError.invalidArchive }
            let existing = try self.prepare(destination, "SELECT rowid,kind,mime,length(payload) FROM event_media WHERE event_key=? AND slot=?")
            defer { sqlite3_finalize(existing) }
            try self.bind(existing, text: eventKey, index: 1)
            guard sqlite3_bind_int64(existing, 2, slot) == SQLITE_OK else { throw ArchiveTransferError.storageUnavailable }
            let found = sqlite3_step(existing)
            let destinationId: Int64, copy: Bool
            if found == SQLITE_ROW {
                guard try self.text(existing, 1, limit: 32) == kind,
                      try self.text(existing, 2, limit: 256) == mime,
                      sqlite3_column_int64(existing, 3) == size else { throw ArchiveTransferError.conflict }
                destinationId = sqlite3_column_int64(existing, 0); copy = false
            } else if found == SQLITE_DONE {
                let insert = try self.prepare(destination, "INSERT INTO event_media(event_key,slot,kind,mime,payload,byte_cost) VALUES(?,?,?,?,zeroblob(?),?)")
                defer { sqlite3_finalize(insert) }
                try self.bind(insert, text: eventKey, index: 1)
                try self.bind(insert, text: kind, index: 3); try self.bind(insert, text: mime, index: 4)
                guard sqlite3_bind_int64(insert, 2, slot) == SQLITE_OK,
                      sqlite3_bind_int64(insert, 5, size) == SQLITE_OK,
                      sqlite3_bind_int64(insert, 6, size + Int64(kind.utf8.count + mime.utf8.count + 64)) == SQLITE_OK,
                      sqlite3_step(insert) == SQLITE_DONE else { throw ArchiveTransferError.storageUnavailable }
                destinationId = sqlite3_last_insert_rowid(destination); copy = true; result += 1
            } else { throw ArchiveTransferError.storageUnavailable }
            try self.copyBlob(source: source, sourceId: sourceId, destination: destination, destinationId: destinationId,
                              size: size, write: copy, cancellation: cancellation)
            code = sqlite3_step(row)
        }
        guard code == SQLITE_DONE else { throw ArchiveTransferError.invalidArchive }
        return result
    }

    private static func copyBlob(source: OpaquePointer, sourceId: Int64, destination: OpaquePointer,
                                 destinationId: Int64, size: Int64, write: Bool,
                                 cancellation: ArchiveTransferCancellation) throws {
        var input: OpaquePointer?, output: OpaquePointer?
        let a = sqlite3_blob_open(source, "main", "event_media", "payload", sourceId, 0, &input)
        let b = sqlite3_blob_open(destination, "main", "event_media", "payload", destinationId, write ? 1 : 0, &output)
        defer { if let input { sqlite3_blob_close(input) }; if let output { sqlite3_blob_close(output) } }
        guard a == SQLITE_OK, b == SQLITE_OK, let input, let output,
              Int64(sqlite3_blob_bytes(input)) == size, Int64(sqlite3_blob_bytes(output)) == size else { throw ArchiveTransferError.invalidArchive }
        var offset: Int64 = 0
        while offset < size {
            try cancellation.check()
            let count = Int(min(size - offset, Int64(ArchiveTransferEnvelope.chunkBytes)))
            var part = Data(count: count)
            let read = part.withUnsafeMutableBytes { sqlite3_blob_read(input, $0.baseAddress, Int32(count), Int32(offset)) }
            guard read == SQLITE_OK else { throw ArchiveTransferError.invalidArchive }
            if write {
                guard part.withUnsafeBytes({ sqlite3_blob_write(output, $0.baseAddress, Int32(count), Int32(offset)) }) == SQLITE_OK else {
                    throw ArchiveTransferError.storageUnavailable
                }
            } else {
                var comparison = Data(count: count)
                guard comparison.withUnsafeMutableBytes({ sqlite3_blob_read(output, $0.baseAddress, Int32(count), Int32(offset)) }) == SQLITE_OK,
                      comparison == part else { throw ArchiveTransferError.conflict }
            }
            offset += Int64(count)
            try cancellation.checkpoint(.mediaChunk)
        }
    }

    private static func schema(_ database: OpaquePointer) throws -> [String: String] {
        // Ignore only known SQLite bookkeeping. A broad reserved-prefix rule
        // would also hide attacker-inserted schema rows in an external backup.
        // SQLite's automatic UNIQUE/PRIMARY KEY indexes have a NULL SQL field;
        // retain and compare those entries using a stable empty representation.
        let query = try self.prepare(database, "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_schema WHERE name NOT IN ('sqlite_sequence','sqlite_stat1','sqlite_stat4') ORDER BY name")
        defer { sqlite3_finalize(query) }
        var result: [String: String] = [:], code = sqlite3_step(query)
        while code == SQLITE_ROW {
            guard result.count < 64 else { throw ArchiveTransferError.invalidArchive }
            let name = try self.text(query, 1, limit: 128)
            result[name] = try self.text(query, 0, limit: 16) + "|" + self.text(query, 2, limit: 128) + "|" + self.text(query, 3, limit: 64 * 1024)
            code = sqlite3_step(query)
        }
        guard code == SQLITE_DONE, !result.isEmpty else { throw ArchiveTransferError.invalidArchive }
        return result
    }

    static func execute(_ database: OpaquePointer, _ sql: String) throws {
        guard sqlite3_exec(database, sql, nil, nil, nil) == SQLITE_OK else { throw ArchiveTransferError.storageUnavailable }
    }

    private static func prepare(_ database: OpaquePointer, _ sql: String) throws -> OpaquePointer {
        var statement: OpaquePointer?
        let code = sqlite3_prepare_v2(database, sql, -1, &statement, nil)
        guard code == SQLITE_OK, let statement else {
            if let statement { sqlite3_finalize(statement) }
            throw ArchiveTransferError.invalidArchive
        }
        return statement
    }

    private static func scalar(_ database: OpaquePointer, _ sql: String) throws -> Int64 {
        let row = try self.prepare(database, sql); defer { sqlite3_finalize(row) }
        guard sqlite3_step(row) == SQLITE_ROW, sqlite3_column_type(row, 0) == SQLITE_INTEGER else { throw ArchiveTransferError.invalidArchive }
        return sqlite3_column_int64(row, 0)
    }

    private static func textScalar(_ database: OpaquePointer, _ sql: String) throws -> String {
        let row = try self.prepare(database, sql); defer { sqlite3_finalize(row) }
        guard sqlite3_step(row) == SQLITE_ROW else { throw ArchiveTransferError.invalidArchive }
        return try self.text(row, 0, limit: 128)
    }

    private static func text(_ row: OpaquePointer, _ column: Int32, limit: Int) throws -> String {
        let size = sqlite3_column_bytes(row, column)
        guard sqlite3_column_type(row, column) == SQLITE_TEXT, size >= 0, size <= limit,
              let bytes = sqlite3_column_text(row, column),
              let text = String(data: Data(bytes: bytes, count: Int(size)), encoding: .utf8) else { throw ArchiveTransferError.invalidArchive }
        return text
    }

    private static func blob(_ row: OpaquePointer, _ column: Int32, limit: Int) throws -> Data {
        let size = sqlite3_column_bytes(row, column)
        guard sqlite3_column_type(row, column) == SQLITE_BLOB, size >= 0, size <= limit else { throw ArchiveTransferError.invalidArchive }
        if size == 0 { return Data() }
        guard let bytes = sqlite3_column_blob(row, column) else { throw ArchiveTransferError.invalidArchive }
        return Data(bytes: bytes, count: Int(size))
    }

    private static func optionalInteger(_ row: OpaquePointer, _ column: Int32) throws -> Int64? {
        if sqlite3_column_type(row, column) == SQLITE_NULL { return nil }
        guard sqlite3_column_type(row, column) == SQLITE_INTEGER else { throw ArchiveTransferError.invalidArchive }
        return sqlite3_column_int64(row, column)
    }

    private static func bind(_ row: OpaquePointer, text: String, index: Int32) throws {
        guard sqlite3_bind_text(row, index, text, Int32(text.utf8.count), self.transient) == SQLITE_OK else { throw ArchiveTransferError.storageUnavailable }
    }

    private static func bind(_ row: OpaquePointer, optional: Int64?, index: Int32) throws {
        let code = optional.map { sqlite3_bind_int64(row, index, $0) } ?? sqlite3_bind_null(row, index)
        guard code == SQLITE_OK else { throw ArchiveTransferError.storageUnavailable }
    }
}
