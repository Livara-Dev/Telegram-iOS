// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import sqlcipher
@testable import LocalMessageArchive

final class ArchiveStoreTests: XCTestCase {
    private final class Keys: ArchiveKeyProvider {
        let value: Data
        private(set) var createRequests: [Bool] = []
        init(_ byte: UInt8 = 0x73) { value = Data(repeating: byte, count: 32) }
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
            createRequests.append(createIfMissing)
            return value
        }
    }
    private var directory: URL!
    private var stores: [ArchiveStore] = []
    private let enabled = ArchiveCapturePolicy(enabled: true)

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("archive-tests-" + UUID().uuidString)
    }
    override func tearDownWithError() throws {
        for store in stores { close(store) }
        if FileManager.default.fileExists(atPath: directory.path) { try FileManager.default.removeItem(at: directory) }
    }
    private func awaitResult<T>(_ operation: (@escaping (Result<T, ArchiveError>) -> Void) -> Void) throws -> T {
        let done = expectation(description: "archive operation")
        var result: Result<T, ArchiveError>?
        operation { result = $0; done.fulfill() }
        wait(for: [done], timeout: 10)
        return try XCTUnwrap(result).get()
    }
    private func open(account: Int64 = 1, keys: Keys = Keys(), now: Int64 = 1000,
                      retention: ArchiveRetentionPolicy = ArchiveRetentionPolicy()) throws -> ArchiveStore {
        let store: ArchiveStore = try awaitResult {
            ArchiveStore.open(baseDirectory: directory, accountId: account, keyProvider: keys,
                              retention: retention, callbackQueue: .main, clock: { now }, completion: $0)
        }
        stores.append(store)
        return store
    }
    private func close(_ store: ArchiveStore) {
        let done = expectation(description: "close")
        store.close { done.fulfill() }
        wait(for: [done], timeout: 10)
    }
    private func event(_ key: String, text: String = "message", account: Int64 = 1, peer: Int64 = 42,
                       message: Int32 = 10, kind: ArchiveEventKind = .deleted, observed: Int64 = 900,
                       edit: Int64? = nil, author: Int64? = 7, entities: Data = Data()) -> ArchiveEvent {
        ArchiveEvent(eventKey: key, message: ArchiveMessageID(accountId: account, peerId: peer, namespace: 0, messageId: message),
                     kind: kind, observedAt: observed, serverEditAt: edit,
                     content: ArchiveContent(text: text, authorId: author, sentAt: 800, entities: entities))
    }
    private func capture(_ event: ArchiveEvent, _ context: ArchiveCaptureContext = ArchiveCaptureContext(ordinaryCloudMessage: true)) -> ArchiveCapture {
        ArchiveCapture(event: event, context: context)
    }
    private func write(_ store: ArchiveStore, _ events: [ArchiveEvent]) throws -> ArchiveWriteResult {
        try awaitResult { store.record(events.map { capture($0) }, policy: enabled, completion: $0) }
    }
    private func records(_ store: ArchiveStore, peer: Int64? = nil) throws -> [ArchivedRecord] {
        try awaitResult { store.deletedMessages(peerId: peer, completion: $0) }
    }
    private func usage(_ store: ArchiveStore) throws -> ArchiveUsage {
        try awaitResult { store.usage(completion: $0) }
    }
    private func assertError<T>(_ expected: ArchiveError, _ operation: () throws -> T, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try operation(), file: file, line: line) { error in
            XCTAssertEqual(error as? ArchiveError, expected, file: file, line: line)
        }
    }

    func testEncryptedFileAndReopen() throws {
        let keys = Keys()
        let store = try open(keys: keys)
        let canary = "unique-private-payload-" + UUID().uuidString
        XCTAssertEqual(try write(store, [event("delete-1", text: canary)]).inserted, 1)
        let location = store.databaseURL
        for suffix in ["", "-wal", "-shm"] {
            let file = URL(fileURLWithPath: location.path + suffix)
            if FileManager.default.fileExists(atPath: file.path) {
                XCTAssertNil(try Data(contentsOf: file).range(of: Data(canary.utf8)))
            }
        }
        close(store)
        let bytes = try Data(contentsOf: location)
        XCTAssertNotEqual(bytes.prefix(16), Data("SQLite format 3\0".utf8))
        XCTAssertNil(bytes.range(of: Data(canary.utf8)))
        let reopened = try open(keys: keys)
        XCTAssertEqual(try records(reopened).first?.event.content.text, canary)
        XCTAssertEqual(keys.createRequests, [true, false])
    }

    func testWrongKeyDoesNotReplaceArchive() throws {
        let store = try open()
        _ = try write(store, [event("a")])
        close(store)
        let before = try Data(contentsOf: store.databaseURL)
        let wrong = Keys(0x31)
        XCTAssertThrowsError(try open(keys: wrong))
        XCTAssertEqual(wrong.createRequests, [false])
        XCTAssertEqual(try Data(contentsOf: store.databaseURL), before)
        XCTAssertEqual(try records(open()).count, 1)
    }

    func testExistingDatabaseNeverRequestsReplacementKey() throws {
        let keys = Keys()
        let store = try open(keys: keys)
        close(store)
        _ = try open(keys: keys)
        XCTAssertEqual(keys.createRequests, [true, false])
    }

    func testEditTimelinePreservesPreviousVersionsAndTimestamps() throws {
        let store = try open()
        let first = event("edit-1", text: "original", kind: .edited, observed: 910, edit: 905)
        let second = event("edit-2", text: "revision", kind: .edited, observed: 920, edit: 915)
        let deleted = event("delete-1", text: "final", observed: 930)
        _ = try write(store, [first, second, deleted])
        let timeline: [ArchivedRecord] = try awaitResult { store.timeline(for: first.message, completion: $0) }
        XCTAssertEqual(timeline.map(\.event), [first, second, deleted])
        XCTAssertEqual(timeline.map(\.event.observedAt), [910, 920, 930])
        XCTAssertEqual(timeline.map(\.event.serverEditAt), [905, 915, nil])
        XCTAssertEqual(try records(store).map(\.event), [deleted])
    }

    func testDuplicateIsIdempotentAndConflictingBatchRollsBack() throws {
        let store = try open()
        let original = event("same", observed: 900)
        _ = try write(store, [original])
        let firstSequence = try XCTUnwrap(records(store).first?.sequence)
        let duplicate = try write(store, [original])
        XCTAssertEqual(duplicate.inserted, 0)
        XCTAssertEqual(duplicate.duplicates, 1)
        assertError(.eventConflict) { try write(store, [event("new"), event("same", text: "changed")]) }
        XCTAssertEqual(try usage(store).records, 1)
        XCTAssertEqual(try records(store).first?.sequence, firstSequence)
        XCTAssertEqual(try records(store).first?.event.observedAt, 900)
    }

    func testAccountAndPeerIsolation() throws {
        let first = try open(account: 1)
        let second = try open(account: 2)
        _ = try write(first, [event("one", peer: 1), event("two", peer: 2)])
        _ = try write(second, [event("one", text: "account two", account: 2)])
        XCTAssertEqual(try records(first, peer: 1).map(\.event.eventKey), ["one"])
        XCTAssertEqual(try records(second).first?.event.content.text, "account two")
        assertError(.accountMismatch) { try write(first, [event("wrong-account", account: 2)]) }
        let wrongID = ArchiveMessageID(accountId: 2, peerId: 42, namespace: 0, messageId: 10)
        assertError(.accountMismatch) { try awaitResult { first.timeline(for: wrongID, completion: $0) } }
    }

    func testCopiedDatabaseCannotBeOpenedAsAnotherAccount() throws {
        let first = try open()
        _ = try write(first, [event("one")]); close(first)
        let destination = directory.appendingPathComponent("account-2")
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: first.databaseURL, to: destination.appendingPathComponent("archive.sqlite"))
        assertError(.accountMismatch) { try open(account: 2) }
    }

    func testPolicyDefaultsAndSensitiveContentAreExcluded() throws {
        let store = try open()
        let defaultResult: ArchiveWriteResult = try awaitResult {
            store.record([capture(event("disabled"))], policy: ArchiveCapturePolicy(), completion: $0)
        }
        XCTAssertEqual(defaultResult.excluded, 1)
        let contexts = [ArchiveCaptureContext(), ArchiveCaptureContext(ordinaryCloudMessage: true, secretChat: true),
                        ArchiveCaptureContext(ordinaryCloudMessage: true, expiring: true),
                        ArchiveCaptureContext(ordinaryCloudMessage: true, viewOnce: true),
                        ArchiveCaptureContext(ordinaryCloudMessage: true, botConversation: true)]
        let result: ArchiveWriteResult = try awaitResult {
            store.record(contexts.enumerated().map { capture(event("excluded-\($0.offset)"), $0.element) }, policy: enabled, completion: $0)
        }
        XCTAssertEqual(result.excluded, 5)
        let excluded = ArchiveCapturePolicy(enabled: true, excludedPeers: [10], excludedSenders: [20])
        let peerSender: ArchiveWriteResult = try awaitResult {
            store.record([capture(event("peer", peer: 10)), capture(event("sender", author: 20))], policy: excluded, completion: $0)
        }
        XCTAssertEqual(peerSender.excluded, 2)
        XCTAssertEqual(try usage(store).records, 0)
    }

    func testAgeAndPayloadRetentionIncludingEventKeyBytes() throws {
        let store = try open(retention: ArchiveRetentionPolicy(maxAgeSeconds: 100, maxPayloadBytes: 132))
        // Each one-byte key + one-byte text + fixed metadata allowance costs 66 logical bytes.
        let result = try write(store, [event("o", text: "a", observed: 899), event("a", text: "a", observed: 900),
                                       event("b", text: "b", observed: 901), event("c", text: "c", observed: 902)])
        XCTAssertEqual(result.removedByRetention, 2)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["c", "b"])
        XCTAssertEqual(try usage(store).logicalPayloadBytes, 132)
    }

    func testReopenPrunesExpiredDataBeforeQuery() throws {
        let policy = ArchiveRetentionPolicy(maxAgeSeconds: 100, maxPayloadBytes: 10000)
        let first = try open(retention: policy)
        _ = try write(first, [event("old", observed: 950)])
        close(first)
        let reopened = try open(now: 1100, retention: policy)
        XCTAssertTrue(try records(reopened).isEmpty)
    }

    func testRetentionChangeAndPurge() throws {
        let store = try open()
        _ = try write(store, [event("old", observed: 800), event("new", observed: 990)])
        let removed: Int = try awaitResult {
            store.setRetention(ArchiveRetentionPolicy(maxAgeSeconds: 20, maxPayloadBytes: 10000), completion: $0)
        }
        XCTAssertEqual(removed, 1)
        let _: Void = try awaitResult { store.purge(completion: $0) }
        XCTAssertEqual(try usage(store).records, 0)
    }

    func testPaginationAndInputBounds() throws {
        let store = try open()
        _ = try write(store, [event("a"), event("b"), event("c")])
        let newest: [ArchivedRecord] = try awaitResult { store.deletedMessages(limit: 2, completion: $0) }
        XCTAssertEqual(newest.map(\.event.eventKey), ["c", "b"])
        let older: [ArchivedRecord] = try awaitResult { store.deletedMessages(beforeSequence: newest.last!.sequence, limit: 2, completion: $0) }
        XCTAssertEqual(older.map(\.event.eventKey), ["a"])
        assertError(.invalidInput) { try awaitResult { store.deletedMessages(limit: 201, completion: $0) } }
        assertError(.invalidInput) { try write(store, [event("", text: "invalid")]) }
        assertError(.invalidInput) { try write(store, [event("future", observed: 1001)]) }
        assertError(.invalidInput) { try write(store, [event("large", text: String(repeating: "x", count: 262145))]) }
        assertError(.invalidInput) { try write(store, (0...256).map { event("batch-\($0)") }) }
        XCTAssertEqual(try usage(store).records, 3)
    }

    func testPreparedBindsPreserveQuotesNulsAndEntityBytes() throws {
        let store = try open()
        let original = event("quote'--", text: "Robert'); DROP TABLE events;--\0tail 💬", entities: Data([0, 255, 1, 0]))
        _ = try write(store, [original])
        XCTAssertEqual(try records(store).first?.event, original)
    }

    func testClosedStoreRejectsOperations() throws {
        let store = try open(); close(store)
        assertError(.closed) { try usage(store) }
    }

    private func rawDatabase(_ url: URL, keys: Keys = Keys(), statements: [String]) throws {
        var handle: OpaquePointer?
        XCTAssertEqual(sqlite3_open(url.path, &handle), SQLITE_OK)
        let db = try XCTUnwrap(handle); defer { sqlite3_close(db) }
        let key = keys.value.map { String(format: "%02x", $0) }.joined()
        for statement in ["PRAGMA key = \"x'\(key)'\"", "PRAGMA cipher_plaintext_header_size=0"] + statements {
            // Do not include SQL text in assertion diagnostics; the setup contains a test key.
            XCTAssertEqual(sqlite3_exec(db, statement, nil, nil, nil), SQLITE_OK)
        }
    }

    func testFutureSchemaIsRejectedWithoutMigration() throws {
        let store = try open(); close(store)
        try rawDatabase(store.databaseURL, statements: ["PRAGMA user_version=3"])
        let before = try Data(contentsOf: store.databaseURL)
        assertError(.unsupportedSchema(3)) { try open() }
        XCTAssertEqual(try Data(contentsOf: store.databaseURL), before)
    }

    private func rawInteger(_ url: URL, query: String) throws -> Int64 {
        var handle: OpaquePointer?
        XCTAssertEqual(sqlite3_open(url.path, &handle), SQLITE_OK)
        let db = try XCTUnwrap(handle); defer { sqlite3_close(db) }
        let key = Keys().value.map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(sqlite3_exec(db, "PRAGMA key = \"x'\(key)'\"", nil, nil, nil), SQLITE_OK)
        XCTAssertEqual(sqlite3_exec(db, "PRAGMA cipher_plaintext_header_size=0", nil, nil, nil), SQLITE_OK)
        var statement: OpaquePointer?
        XCTAssertEqual(sqlite3_prepare_v2(db, query, -1, &statement, nil), SQLITE_OK)
        let prepared = try XCTUnwrap(statement); defer { sqlite3_finalize(prepared) }
        XCTAssertEqual(sqlite3_step(prepared), SQLITE_ROW)
        return sqlite3_column_int64(prepared, 0)
    }

    /// A genuine encrypted schema-1 fixture, independent of the current store's schema creation.
    private func legacyV1Database(extraStatements: [String] = []) throws -> URL {
        let accountDirectory = directory.appendingPathComponent("account-1")
        try FileManager.default.createDirectory(at: accountDirectory, withIntermediateDirectories: true)
        let url = accountDirectory.appendingPathComponent("archive.sqlite")
        try rawDatabase(url, statements: [
            "CREATE TABLE archive_meta(id INTEGER PRIMARY KEY CHECK(id=1),account_id INTEGER NOT NULL)",
            "INSERT INTO archive_meta VALUES(1,1)",
            """
            CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL,peer_id INTEGER NOT NULL,namespace INTEGER NOT NULL,message_id INTEGER NOT NULL,
            kind INTEGER NOT NULL CHECK(kind IN(1,2)),observed_at INTEGER NOT NULL,server_edit_at INTEGER,
            text TEXT NOT NULL,author_id INTEGER,sent_at INTEGER NOT NULL,entities BLOB NOT NULL,payload_bytes INTEGER NOT NULL)
            """,
            "CREATE INDEX events_message ON events(account_id,peer_id,namespace,message_id,sequence)",
            "CREATE INDEX events_deleted ON events(account_id,kind,sequence)",
            "CREATE INDEX events_retention ON events(observed_at,sequence)",
            "INSERT INTO events VALUES(7,'legacy-edit',1,42,0,10,2,910,905,'original',7,800,X'00FF',85)",
            "INSERT INTO events VALUES(9,'legacy-delete',1,42,0,10,1,930,NULL,'final',7,800,X'',82)",
            "PRAGMA user_version=1"
        ] + extraStatements)
        return url
    }

    func testVersionOneMigrationPreservesHistoryAndInitializesCounters() throws {
        let url = try legacyV1Database()
        let store = try open()
        let expected = [event("legacy-edit", text: "original", kind: .edited, observed: 910, edit: 905, entities: Data([0, 255])),
                        event("legacy-delete", text: "final", observed: 930)]
        let timeline: [ArchivedRecord] = try awaitResult { store.timeline(for: expected[0].message, completion: $0) }
        XCTAssertEqual(timeline.map(\.event), expected)
        XCTAssertEqual(timeline.map(\.sequence), [7, 9])
        XCTAssertEqual(try usage(store), ArchiveUsage(records: 2, logicalPayloadBytes: 167))
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 2)
        _ = try write(store, [event("after-migration")])
        XCTAssertEqual(try usage(store).records, 3)
        close(store)
        let reopened = try open()
        XCTAssertEqual(try usage(reopened).records, 3)
        XCTAssertEqual(try records(reopened).first?.sequence, 10)
    }

    func testMigrationFailureRollsBackSchemaAndData() throws {
        let url = try legacyV1Database(extraStatements: [
            // Name collision occurs after usage creation and earlier trigger creation.
            "CREATE TRIGGER archive_events_delete_usage AFTER DELETE ON events BEGIN SELECT 1; END"
        ])
        XCTAssertThrowsError(try open())
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 1)
        XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM events"), 2)
        XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM sqlite_master WHERE name='archive_usage'"), 0)
        XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM sqlite_master WHERE name='archive_events_insert_usage'"), 0)
        // Resolve the malformed legacy fixture; a clean retry must migrate successfully.
        try rawDatabase(url, statements: ["DROP TRIGGER archive_events_delete_usage"])
        XCTAssertEqual(try usage(open()).records, 2)
    }

    func testMigrationRejectsInvalidSizesBeforeMutation() throws {
        let url = try legacyV1Database()
        for invalidSize in ["-1", "1.5", "'invalid'", "0"] {
            try rawDatabase(url, statements: ["UPDATE events SET payload_bytes=\(invalidSize) WHERE sequence=7"])
            assertError(.invalidInput) { try open() }
            XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 1)
            XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM sqlite_master WHERE name='archive_usage'"), 0)
        }
    }

    func testMigrationRejectsWrongMetadataAndRowAccountsBeforeMutation() throws {
        let url = try legacyV1Database(extraStatements: ["UPDATE archive_meta SET account_id=2"])
        assertError(.accountMismatch) { try open() }
        try rawDatabase(url, statements: ["UPDATE archive_meta SET account_id=1", "UPDATE events SET account_id=2 WHERE sequence=7"])
        assertError(.accountMismatch) { try open() }
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 1)
        XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM sqlite_master WHERE name='archive_usage'"), 0)
    }

    func testCounterTriggersRejectUpdatesAndInvalidInsertedSizes() throws {
        let store = try open()
        _ = try write(store, [event("immutable")]); close(store)
        var handle: OpaquePointer?
        XCTAssertEqual(sqlite3_open(store.databaseURL.path, &handle), SQLITE_OK)
        let db = try XCTUnwrap(handle); defer { sqlite3_close(db) }
        let key = Keys().value.map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(sqlite3_exec(db, "PRAGMA key = \"x'\(key)'\"", nil, nil, nil), SQLITE_OK)
        XCTAssertEqual(sqlite3_exec(db, "PRAGMA cipher_plaintext_header_size=0", nil, nil, nil), SQLITE_OK)
        XCTAssertEqual(sqlite3_exec(db, "UPDATE events SET text='changed',payload_bytes=0", nil, nil, nil), SQLITE_CONSTRAINT)
        XCTAssertEqual(sqlite3_exec(db, "INSERT INTO events VALUES(20,'invalid',1,42,0,10,1,900,NULL,'text',7,800,X'',0)", nil, nil, nil), SQLITE_CONSTRAINT)
        let reopened = try open()
        XCTAssertEqual(try usage(reopened).records, 1)
        XCTAssertEqual(try records(reopened).first?.event.content.text, "message")
    }

    func testCountersStayConsistentAcrossConnectionsRollbackAndPurge() throws {
        let first = try open(), second = try open()
        _ = try write(first, [event("first", text: "one")])
        _ = try write(second, [event("second", text: "two")])
        let baseline = try usage(first)
        XCTAssertEqual(try usage(second), baseline)
        XCTAssertEqual(baseline.records, 2)
        assertError(.eventConflict) { try write(second, [event("rolled-back", text: "large payload"), event("first", text: "conflict")]) }
        XCTAssertEqual(try usage(first), baseline)
        XCTAssertEqual(try usage(second), baseline)
        let _: Void = try awaitResult { first.purge(completion: $0) }
        XCTAssertEqual(try usage(second), ArchiveUsage(records: 0, logicalPayloadBytes: 0))
        _ = try write(second, [event("after-purge")])
        XCTAssertEqual(try usage(first).records, 1)
    }

    func testRetentionDeletesMultipleChunksAndOnlyRequiredQuotaRows() throws {
        let store = try open()
        for start in stride(from: 0, to: 600, by: 200) {
            _ = try write(store, (start..<(start + 200)).map {
                event(String(format: "s%03d", $0), text: "a", message: Int32($0 + 1), observed: $0 < 300 ? 800 : 990)
            })
        }
        // 300 expired records cross a chunk boundary. 300 remaining cost 69 bytes each;
        // retain exactly 17, requiring a full quota chunk plus a partial chunk.
        let removed: Int = try awaitResult {
            store.setRetention(ArchiveRetentionPolicy(maxAgeSeconds: 100, maxPayloadBytes: 17 * 69), completion: $0)
        }
        XCTAssertEqual(removed, 583)
        XCTAssertEqual(try usage(store), ArchiveUsage(records: 17, logicalPayloadBytes: 17 * 69))
        XCTAssertEqual(try records(store).map(\.event.eventKey), (583..<600).reversed().map { String(format: "s%03d", $0) })
    }

    func testUnknownUnversionedDatabaseIsNotAdopted() throws {
        let accountDirectory = directory.appendingPathComponent("account-1")
        try FileManager.default.createDirectory(at: accountDirectory, withIntermediateDirectories: true)
        let url = accountDirectory.appendingPathComponent("archive.sqlite")
        try rawDatabase(url, statements: ["CREATE TABLE foreign_data(value TEXT)"])
        assertError(.unsupportedSchema(0)) { try open() }
    }

    func testConnectionOverridesPostboxPlaintextHeaderDefault() throws {
        var db: OpaquePointer?
        XCTAssertEqual(sqlite3_open(":memory:", &db), SQLITE_OK)
        var query: OpaquePointer?
        XCTAssertEqual(sqlite3_prepare_v2(db, "PRAGMA cipher_default_plaintext_header_size", -1, &query, nil), SQLITE_OK)
        XCTAssertEqual(sqlite3_step(query), SQLITE_ROW)
        let previousDefault = sqlite3_column_int(query, 0)
        sqlite3_finalize(query)
        defer {
            sqlite3_exec(db, "PRAGMA cipher_default_plaintext_header_size=\(previousDefault)", nil, nil, nil)
            sqlite3_close(db)
        }
        XCTAssertEqual(sqlite3_exec(db, "PRAGMA cipher_default_plaintext_header_size=32", nil, nil, nil), SQLITE_OK)
        let store = try open()
        _ = try write(store, [event("header")]); close(store)
        let bytes = try Data(contentsOf: store.databaseURL)
        XCTAssertNotEqual(bytes.prefix(16), Data("SQLite format 3\0".utf8))
        XCTAssertEqual(try records(open()).count, 1)
    }

    func testDanglingDatabaseAndSidecarSymlinksAreRejected() throws {
        let accountDirectory = directory.appendingPathComponent("account-1")
        try FileManager.default.createDirectory(at: accountDirectory, withIntermediateDirectories: true)
        for suffix in ["", "-wal", "-shm", "-journal"] {
            let endpoint = accountDirectory.appendingPathComponent("archive.sqlite" + suffix)
            let target = directory.appendingPathComponent("should-not-exist")
            try FileManager.default.createSymbolicLink(at: endpoint, withDestinationURL: target)
            let keys = Keys()
            XCTAssertThrowsError(try open(keys: keys))
            XCTAssertTrue(keys.createRequests.isEmpty)
            XCTAssertFalse(FileManager.default.fileExists(atPath: target.path))
            try FileManager.default.removeItem(at: endpoint)
        }
    }

    func testSystemOrCallerParentAliasResolvesWithoutFollowingArchiveEndpoint() throws {
        let physicalParent = directory.appendingPathComponent("physical-parent")
        let alias = directory.appendingPathComponent("parent-alias")
        try FileManager.default.createDirectory(at: physicalParent, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: physicalParent)
        let base = alias.appendingPathComponent("owned-archive")
        let store: ArchiveStore = try awaitResult {
            ArchiveStore.open(baseDirectory: base, accountId: 1, keyProvider: Keys(),
                              callbackQueue: .main, clock: { 1000 }, completion: $0)
        }
        stores.append(store)
        XCTAssertFalse(store.databaseURL.path.contains("parent-alias"))
        _ = try write(store, [event("alias-regression")])
        XCTAssertEqual(try records(store).count, 1)
        close(store)
        // A link at the archive-owned endpoint itself remains forbidden.
        let endpointLink = directory.appendingPathComponent("linked-archive")
        try FileManager.default.createSymbolicLink(at: endpointLink, withDestinationURL: base)
        let keys = Keys()
        assertError(.protectionUnavailable) {
            try awaitResult {
                ArchiveStore.open(baseDirectory: endpointLink, accountId: 1, keyProvider: keys, completion: $0)
            }
        }
        XCTAssertTrue(keys.createRequests.isEmpty)
    }

    func testOrphanRollbackJournalIsRejectedWithoutKeyCreation() throws {
        let accountDirectory = directory.appendingPathComponent("account-1")
        try FileManager.default.createDirectory(at: accountDirectory, withIntermediateDirectories: true)
        try Data([1, 2, 3]).write(to: accountDirectory.appendingPathComponent("archive.sqlite-journal"))
        let keys = Keys()
        assertError(.invalidInput) { try open(keys: keys) }
        XCTAssertTrue(keys.createRequests.isEmpty)
    }

    /// Hosted-macOS end-to-end append timing, including async completion/XCTest overhead.
    /// This supplies a before-change measurement, not an iPhone latency or pass/fail target.
    func testBenchmarkSingleAppendAfterTenThousandSnapshots() throws {
        let store = try open()
        let seedCount = 10_000
        for start in stride(from: 0, to: seedCount, by: 256) {
            let end = min(seedCount, start + 256)
            let batch = (start..<end).map {
                event("benchmark-seed-\($0)", text: "synthetic archive benchmark", message: Int32($0 + 1))
            }
            XCTAssertEqual(try write(store, batch).inserted, batch.count)
        }
        XCTAssertEqual(try usage(store).records, Int64(seedCount))
        var samples: [Double] = []
        for iteration in 0...10 {
            let appended = event("benchmark-append-\(iteration)", text: "synthetic archive benchmark",
                                 message: Int32(seedCount + iteration + 1))
            let started = DispatchTime.now().uptimeNanoseconds
            let result = try write(store, [appended])
            let elapsed = DispatchTime.now().uptimeNanoseconds - started
            XCTAssertEqual(result.inserted, 1)
            if iteration > 0 { samples.append(Double(elapsed) / 1_000_000) }
        }
        XCTAssertEqual(try usage(store).records, Int64(seedCount + 11))
        let report: [String: Any] = [
            "benchmark": "archive_single_append", "synthetic_seed_records": seedCount,
            "warmup_appends": 1, "samples_ms": samples,
            "mean_ms": samples.reduce(0, +) / Double(samples.count),
            "measurement": "monotonic async API roundtrip including XCTest expectation/wait overhead",
            "latency_threshold": "none", "iphone_performance_claim": false
        ]
        let reportData = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        print(String(decoding: reportData, as: UTF8.self))
    }
}
