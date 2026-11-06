// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Darwin
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
                      retention: ArchiveRetentionPolicy = ArchiveRetentionPolicy(), clock: (() -> Int64)? = nil) throws -> ArchiveStore {
        let store: ArchiveStore = try awaitResult {
            ArchiveStore.open(baseDirectory: directory, accountId: account, keyProvider: keys,
                              retention: retention, callbackQueue: .main, clock: clock ?? { now }, completion: $0)
        }
        stores.append(store)
        return store
    }

    private func history(_ store: ArchiveStore, peer: Int64 = 42, ids: ClosedRange<Int32> = 1...Int32.max,
                         sent: ClosedRange<Int64> = 0...Int64(Int32.max), excluding: Set<Int64> = [], limit: Int = 40) throws -> ArchiveDeletedHistoryPage {
        return try self.awaitResult { store.deletedHistory(peerId: peer, namespace: 0, messageIds: ids, sentAt: sent,
            excludingAuthors: excluding, limit: limit, completion: $0) }
    }

    func testInlineDeletedHistoryReturnsLatestDeletionPerIdWithinCanonicalWindow() throws {
        let store = try self.open()
        _ = try self.write(store, [self.event("first", text: "old", message: 10), self.event("latest", text: "new", message: 10),
            self.event("only-edit", message: 11, kind: .edited), self.event("inside", message: 12), self.event("outside", message: 14),
            self.event("other-peer", peer: 43, message: 12)])
        let page = try self.history(store, ids: 10...12, sent: 800...800)
        XCTAssertEqual(page.records.map(\.event.eventKey), ["inside", "latest"])
        XCTAssertEqual(page.records.last?.event.content.text, "new")
        XCTAssertFalse(page.isTruncated)
        XCTAssertTrue(try self.history(store, ids: 10...12, sent: 801...900).records.isEmpty)
    }

    func testInlineDeletedHistoryBoundReportsMoreAndRetainsFullSeparateTimeline() throws {
        let store = try self.open()
        _ = try self.write(store, (1...45).map { self.event("bounded-\($0)", message: Int32($0)) })
        let page = try self.history(store)
        XCTAssertEqual(page.records.count, 40)
        XCTAssertEqual(page.records.first?.event.message.messageId, 45)
        XCTAssertEqual(page.records.last?.event.message.messageId, 6)
        XCTAssertTrue(page.isTruncated)
        XCTAssertEqual(try self.records(store).count, 45, "a presentation bound must not delete stored history")
    }

    func testInlineDeletedHistoryAuthorExclusionHappensBeforeReturningPlaintext() throws {
        let store = try self.open()
        _ = try self.write(store, [self.event("blocked", text: "excluded-author-canary", message: 1, author: 7),
            self.event("unknown", text: "unknown-author-canary", message: 2, author: nil),
            self.event("allowed", text: "allowed body", message: 3, author: 8)])
        XCTAssertEqual(try self.history(store, excluding: [7]).records.map(\.event.eventKey), ["allowed"])
        let maximumExclusions = Set((10000..<14095).map(Int64.init)).union([7])
        XCTAssertEqual(maximumExclusions.count, ArchiveDeletedHistoryPage.maximumExcludedAuthors)
        XCTAssertEqual(try self.history(store, excluding: maximumExclusions).records.map(\.event.eventKey), ["allowed"])
        XCTAssertEqual(try self.history(store).records.count, 3)
    }

    func testInlineDeletedHistoryHasAnAccountBoundAfterReopen() throws {
        let first = try self.open(account: 1)
        let second = try self.open(account: 2, keys: Keys(0x74))
        _ = try self.write(first, [self.event("account-one", text: "one")])
        _ = try self.write(second, [self.event("account-two", text: "two", account: 2)])
        self.close(first)
        XCTAssertEqual(try self.history(self.open(account: 1)).records.map(\.event.content.text), ["one"])
        XCTAssertEqual(try self.history(second).records.map(\.event.content.text), ["two"])
    }

    func testInlineDeletedExcerptAndSavedMediaRemainEncryptedAndFullTimelineUnchanged() throws {
        let store = try self.open()
        let body = String(repeating: "encrypted-inline-canary-", count: 200)
        _ = try self.write(store, [self.event("excerpt-media", text: body)])
        XCTAssertTrue(try self.awaitResult { store.recordMedia(eventKey: "excerpt-media", slot: 0, kind: "video", mimeType: "video/mp4",
            data: Data("independent-media-canary".utf8), generation: 0, completion: $0) })
        let page = try self.history(store)
        XCTAssertEqual(page.records.first?.event.content.text, String(body.prefix(2048)))
        XCTAssertEqual(page.records.first?.storedMediaCount, 1)
        XCTAssertEqual(try self.records(store).first?.event.content.text, body)
        for path in [store.databaseURL.path, store.databaseURL.path + "-wal", store.databaseURL.path + "-shm"] where FileManager.default.fileExists(atPath: path) {
            XCTAssertNil(try Data(contentsOf: URL(fileURLWithPath: path)).range(of: Data("encrypted-inline-canary-".utf8)))
        }
    }

    func testInlineDeletedReadHonorsAgePolicyWithoutCreatingOrPruningRowsOnRead() throws {
        var now: Int64 = 1000
        let store = try self.open(retention: ArchiveRetentionPolicy(maxAgeSeconds: 200), clock: { now })
        _ = try self.write(store, [self.event("age-bound", observed: 900)])
        XCTAssertEqual(try self.history(store).records.count, 1)
        now = 1200
        XCTAssertTrue(try self.history(store).records.isEmpty)
        XCTAssertEqual(try self.records(store).count, 1, "inline history reads do not write or change the canonical archive")
    }

    func testInlineDeletedHistoryRejectsUnboundedOrNonCloudRequests() throws {
        let store = try self.open()
        for limit in [0, 41] {
            self.assertError(.invalidInput) { _ = try self.history(store, limit: limit) }
        }
        self.assertError(.invalidInput) { _ = try self.history(store, ids: 0...1) }
        self.assertError(.invalidInput) { _ = try self.history(store, sent: -1...1) }
        self.assertError(.invalidInput) { _ = try self.history(store, sent: 0...(Int64(Int32.max) + 1)) }
        self.assertError(.invalidInput) {
            _ = try self.awaitResult { store.deletedHistory(peerId: 42, namespace: 1, messageIds: 1...100, sentAt: 0...1000,
                excludingAuthors: [], completion: $0) }
        }
        self.assertError(.invalidInput) { _ = try self.history(store, excluding: Set((1...4097).map(Int64.init))) }
    }

    func testPathologicalVersionCountFailsClosedAndDoesNotLeaveProgressHandlerInstalled() throws {
        let store = try self.open()
        // One repeatedly observed id forces the query to inspect old versions;
        // normal one-row reads must remain usable after the bounded failure.
        for batch in 0..<32 {
            _ = try self.write(store, (0..<256).map { self.event("work-\(batch)-\($0)", text: "version-\(batch)-\($0)") })
        }
        do {
            _ = try self.history(store)
            XCTFail("pathological query must hit its independent VM-work budget")
        } catch let error as ArchiveError {
            XCTAssertEqual(error, .unavailable(SQLITE_INTERRUPT))
        }
        XCTAssertEqual(try self.usage(store).records, 8192)
        XCTAssertEqual(try self.awaitResult { store.timeline(for: ArchiveMessageID(accountId: 1, peerId: 42, namespace: 0, messageId: 10), limit: 1, completion: $0) }.count, 1)
    }

    private final class MutationLog {
        private let lock = NSLock()
        private var value = 0
        var count: Int { self.lock.lock(); defer { self.lock.unlock() }; return self.value }
        func record() { self.lock.lock(); self.value += 1; self.lock.unlock() }
    }

    private func observe(_ store: ArchiveStore, log: MutationLog?) {
        let installed = expectation(description: "serial mutation observer installed")
        store.setMutationObserver(log.map { log in { log.record() } }, completion: { installed.fulfill() })
        wait(for: [installed], timeout: 10)
    }

    func testMutationObserverReportsCommitBeforeCompletionAndNoOpReadsDoNotLoop() throws {
        let store = try open()
        let log = MutationLog()
        observe(store, log: log)
        _ = try awaitResult { completion in
            store.record([capture(event("observed"))], generation: 0, policy: enabled) { result in
                XCTAssertEqual(log.count, 1, "serial callback queue receives committed invalidation before result")
                completion(result)
            }
        }
        _ = try records(store)
        _ = try usage(store)
        XCTAssertEqual(try awaitResult { store.setRetention(ArchiveRetentionPolicy(), completion: $0) }, 0)
        XCTAssertNil(try awaitResult { store.mediaInfo(eventKey: "observed", slot: 0, completion: $0) })
        _ = try write(store, [event("observed")])
        XCTAssertEqual(log.count, 1, "no-op pruning and duplicate reads/writes cannot invalidate their own lookup forever")
        observe(store, log: nil)
        _ = try write(store, [event("unobserved")])
        XCTAssertEqual(log.count, 1)
    }

    func testMutationObserverIgnoresRolledBackInsertAndResetsBaselineForLaterRead() throws {
        let store = try open()
        _ = try write(store, [event("existing")])
        let log = MutationLog()
        observe(store, log: log)
        assertError(.eventConflict) { try write(store, [event("rolled-back"), event("existing", text: "conflict")]) }
        XCTAssertEqual(log.count, 0)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["existing"])
        XCTAssertEqual(try awaitResult { store.setRetention(ArchiveRetentionPolicy(), completion: $0) }, 0)
        XCTAssertEqual(log.count, 0, "SQLite total_changes includes rollback work; each new transaction needs its own baseline")
        _ = try write(store, [event("committed")])
        XCTAssertEqual(log.count, 1)
    }

    func testMutationObserverReportsPruneInsideMediaReadWithoutAnInterveningWrite() throws {
        final class Clock {
            private let lock = NSLock()
            private var value: Int64 = 1000
            func read() -> Int64 { self.lock.lock(); defer { self.lock.unlock() }; return self.value }
            func advance() { self.lock.lock(); self.value = 2000; self.lock.unlock() }
        }
        let clock = Clock()
        let store = try open(retention: ArchiveRetentionPolicy(maxAgeSeconds: 50), clock: { clock.read() })
        _ = try write(store, [event("internal-prune", observed: 990)])
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "internal-prune", slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1, 2, 3]), generation: 0, completion: $0) })
        let log = MutationLog()
        observe(store, log: log)
        clock.advance()
        XCTAssertNil(try awaitResult { store.media(eventKey: "internal-prune", slot: 0, completion: $0) })
        XCTAssertEqual(log.count, 1)
        XCTAssertEqual(try usage(store).records, 0)
        XCTAssertNil(try awaitResult { store.media(eventKey: "internal-prune", slot: 0, completion: $0) })
        XCTAssertEqual(log.count, 1)
    }

    func testMutationObserverReportsMediaAndSnapshotDeleteButNotRepeatedNoOpDelete() throws {
        let store = try open()
        _ = try write(store, [event("media-delete")])
        let log = MutationLog()
        observe(store, log: log)
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "media-delete", slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1, 2, 3]), generation: 0, completion: $0) })
        XCTAssertEqual(log.count, 1)
        XCTAssertTrue(try awaitResult { store.deleteSnapshot(eventKey: "media-delete", generation: 0, completion: $0) })
        XCTAssertEqual(log.count, 2)
        XCTAssertTrue(try awaitResult { store.deleteSnapshot(eventKey: "media-delete", generation: 0, completion: $0) })
        XCTAssertEqual(log.count, 2)
    }

    func testMutationObserverIsAccountScopedAndIsClearedOnClose() throws {
        let first = try open()
        let second = try open(account: 2, keys: Keys(0x74))
        let firstLog = MutationLog()
        let secondLog = MutationLog()
        observe(first, log: firstLog)
        observe(second, log: secondLog)
        _ = try write(first, [event("same")])
        XCTAssertEqual(firstLog.count, 1)
        XCTAssertEqual(secondLog.count, 0)
        _ = try write(second, [event("same", account: 2)])
        XCTAssertEqual(firstLog.count, 1)
        XCTAssertEqual(secondLog.count, 1)
        close(first)
        observe(first, log: firstLog)
        assertError(.closed) { try write(first, [event("closed")]) }
        XCTAssertEqual(firstLog.count, 1)
        _ = try purge(second, to: 1)
        XCTAssertEqual(secondLog.count, 2)
    }

    func testNoAgeExpiryRetainsOldRecordAcrossLaterReopen() throws {
        let store = try open()
        _ = try write(store, [event("old-record", observed: 0)])
        close(store)
        let reopened = try open(now: 100_000_000)
        XCTAssertEqual(try records(reopened).map(\.event.eventKey), ["old-record"])
    }

    func testDeleteSnapshotRemovesMediaAndIndexesWithoutOutboxResurrection() throws {
        let store = try open()
        let deleted = event("delete-one", text: "uniquely searchable content")
        _ = try writeOutbox(store, deleted, index: 4, generation: 0)
        _ = try writeOutbox(store, event("keep-one"), index: 5, generation: 0)
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "delete-one", slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1, 2, 3]), generation: 0, completion: $0) })
        let isolated = try open(account: 2, keys: Keys(0x74))
        _ = try write(isolated, [event("delete-one", account: 2)])
        XCTAssertTrue(try awaitResult { store.deleteSnapshot(eventKey: "delete-one", generation: 0, completion: $0) })
        XCTAssertTrue(try awaitResult { store.deleteSnapshot(eventKey: "delete-one", generation: 0, completion: $0) })
        XCTAssertNil(try awaitResult { store.mediaInfo(eventKey: "delete-one", slot: 0, completion: $0) })
        XCTAssertTrue(try awaitResult { store.mediaItems(completion: $0) }.isEmpty)
        XCTAssertTrue(try awaitResult { store.searchMessages(query: "uniquely", completion: $0) }.isEmpty)
        _ = try writeOutbox(store, deleted, index: 4, generation: 0)
        XCTAssertFalse(try awaitResult { store.recordMedia(eventKey: "delete-one", slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1, 2, 3]), generation: 0, completion: $0) })
        close(store)
        XCTAssertEqual(try records(open()).map(\.event.eventKey), ["keep-one"])
        XCTAssertEqual(try records(isolated).map(\.event.eventKey), ["delete-one"])
    }

    func testDeleteSnapshotRejectsStaleGenerationWithoutChangingData() throws {
        let store = try open()
        _ = try purge(store, to: 1)
        _ = try write(store, [event("keep-current")], generation: 1)
        assertError(.generationMismatch(expected: 1, received: 0)) {
            try awaitResult { store.deleteSnapshot(eventKey: "keep-current", generation: 0, completion: $0) }
        }
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["keep-current"])
    }

    func testMediaBytesSurviveReopenAndAreRemovedWithEvent() throws {
        let store = try open()
        _ = try write(store, [event("media")])
        let bytes = Data("synthetic-private-media-canary".utf8)
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "media", slot: 0, kind: "photo", mimeType: "image/jpeg", data: bytes, generation: 0, completion: $0) })
        XCTAssertEqual(try awaitResult { store.media(eventKey: "media", slot: 0, completion: $0) }?.data, bytes)
        for path in [store.databaseURL.path, store.databaseURL.path + "-wal", store.databaseURL.path + "-shm"] where FileManager.default.fileExists(atPath: path) {
            XCTAssertNil(try Data(contentsOf: URL(fileURLWithPath: path)).range(of: bytes))
        }
        close(store)
        let reopened = try open()
        XCTAssertEqual(try awaitResult { reopened.media(eventKey: "media", slot: 0, completion: $0) }?.data, bytes)
        XCTAssertEqual(try records(reopened).first?.storedMediaCount, 1)
        _ = try awaitResult { reopened.purge(advancingToGeneration: 1, completion: $0) }
        XCTAssertNil(try awaitResult { reopened.media(eventKey: "media", slot: 0, completion: $0) })
        XCTAssertEqual(try usage(reopened).logicalPayloadBytes, 0)
        XCTAssertFalse(try Data(contentsOf: reopened.databaseURL).range(of: bytes) != nil)
    }

    func testDirectMediaReadEnforcesAgeRetentionWhileStoreStaysOpen() throws {
        final class Clock {
            private let lock = NSLock()
            private var value: Int64 = 1000
            func read() -> Int64 { lock.lock(); defer { lock.unlock() }; return value }
            func advance() { lock.lock(); value = 2000; lock.unlock() }
        }
        let clock = Clock()
        let store = try open(retention: ArchiveRetentionPolicy(maxAgeSeconds: 50), clock: { clock.read() })
        _ = try write(store, [event("expires", observed: 990)])
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "expires", slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1, 2, 3]), generation: 0, completion: $0) })
        XCTAssertNotNil(try awaitResult { store.media(eventKey: "expires", slot: 0, completion: $0) })
        clock.advance()
        // No intervening write, list refresh, close, or reopen is needed.
        XCTAssertNil(try awaitResult { store.media(eventKey: "expires", slot: 0, completion: $0) })
        XCTAssertEqual(try usage(store).records, 0)
        XCTAssertEqual(try usage(store).logicalPayloadBytes, 0)
        XCTAssertTrue(try records(store).isEmpty)
    }

    func testEncryptedPhraseSearchIsLiteralScopedAndPaged() throws {
        let store = try open()
        _ = try write(store, [event("first", text: "Café meeting notes", peer: 10), event("second", text: "cafe meeting again", peer: 10, kind: .edited), event("other", text: "cafe meeting notes", peer: 20)])
        let all = try awaitResult { store.searchMessages(query: "CAFÉ meeting", completion: $0) }
        XCTAssertEqual(all.map(\.event.eventKey), ["other", "second", "first"])
        let scoped = try awaitResult { store.searchMessages(query: "cafe meeting", peerId: 10, kind: .deleted, completion: $0) }
        XCTAssertEqual(scoped.map(\.event.eventKey), ["first"])
        let page = try awaitResult { store.searchMessages(query: "cafe", limit: 1, completion: $0) }
        let cursor = try XCTUnwrap(page.first?.sequence)
        let next = try awaitResult { store.searchMessages(query: "cafe", beforeSequence: cursor, limit: 1, completion: $0) }
        XCTAssertEqual(next.map(\.event.eventKey), ["second"])
        XCTAssertTrue(try awaitResult { store.searchMessages(query: "cafe OR missing", completion: $0) }.isEmpty)
        XCTAssertTrue(try awaitResult { store.searchMessages(query: "\" OR \"", completion: $0) }.isEmpty)
        let other = try open(account: 2, keys: Keys(0x75))
        XCTAssertTrue(try awaitResult { other.searchMessages(query: "cafe", completion: $0) }.isEmpty)
        close(store)
        let reopened = try open()
        XCTAssertEqual(try awaitResult { reopened.searchMessages(query: "cafe", completion: $0) }.count, 3)
        _ = try purge(reopened, to: 1)
        XCTAssertTrue(try awaitResult { reopened.searchMessages(query: "cafe", completion: $0) }.isEmpty)
    }

    func testVersionFiveSearchMigrationIndexesExistingEvents() throws {
        let store = try open()
        _ = try write(store, [event("migrated", text: "historical archive text")])
        let url = store.databaseURL
        close(store)
        try rawDatabase(url, statements: ["DROP TRIGGER archive_gallery_insert", "DROP TRIGGER archive_gallery_delete", "DROP TABLE media_gallery", "DROP TRIGGER archive_search_insert", "DROP TRIGGER archive_search_delete", "DROP TABLE events_search", "PRAGMA user_version=5"])
        let reopened = try open()
        XCTAssertEqual(try awaitResult { reopened.searchMessages(query: "historical archive", completion: $0) }.map(\.event.eventKey), ["migrated"])
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 8)
    }

    func testSearchMigrationFailureRollsBackEarlierSchemaChanges() throws {
        let url = try legacyV1Database(extraStatements: ["CREATE TABLE events_search(unexpected INTEGER)"])
        XCTAssertThrowsError(try open())
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 1)
        XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM events"), 2)
        try rawDatabase(url, statements: ["DROP TABLE events_search"])
        let recovered = try open()
        XCTAssertEqual(try awaitResult { recovered.searchMessages(query: "original", completion: $0) }.count, 1)
    }

    func testSearchIndexTracksRetentionAndRejectsInvalidQueries() throws {
        let store = try open(retention: ArchiveRetentionPolicy(maxPayloadBytes: 150))
        _ = try write(store, [event("old", text: "removed search token", observed: 900)])
        _ = try write(store, [event("new", text: "current token", observed: 950)])
        XCTAssertTrue(try awaitResult { store.searchMessages(query: "removed", completion: $0) }.isEmpty)
        XCTAssertEqual(try awaitResult { store.searchMessages(query: "current", completion: $0) }.count, 1)
        for query in ["", "  ", String(repeating: "x", count: 513), "bad\0query"] {
            assertError(.invalidInput) { try awaitResult { store.searchMessages(query: query, completion: $0) } }
        }
    }

    func testMediaCannotCrossAccountOrPurgeGeneration() throws {
        let first = try open()
        let second = try open(account: 2, keys: Keys(0x74))
        _ = try write(first, [event("same-key")])
        _ = try write(second, [event("same-key", account: 2)])
        XCTAssertTrue(try awaitResult { first.recordMedia(eventKey: "same-key", slot: 0, kind: "video", mimeType: "video/mp4", data: Data([7]), generation: 0, completion: $0) })
        XCTAssertNil(try awaitResult { second.media(eventKey: "same-key", slot: 0, completion: $0) })
        _ = try awaitResult { first.purge(advancingToGeneration: 1, completion: $0) }
        assertError(.generationMismatch(expected: 1, received: 0)) {
            try awaitResult { first.recordMedia(eventKey: "same-key", slot: 0, kind: "video", mimeType: "video/mp4", data: Data([7]), generation: 0, completion: $0) }
        }
        XCTAssertEqual(try usage(second).records, 1)
    }

    func testMediaQuotaEvictsParentAndDoesNotResurrectIt() throws {
        let store = try open(retention: ArchiveRetentionPolicy(maxPayloadBytes: 512))
        _ = try write(store, [event("limited")])
        XCTAssertFalse(try awaitResult { store.recordMedia(eventKey: "limited", slot: 0, kind: "document", mimeType: "application/octet-stream", data: Data(repeating: 9, count: 1024), generation: 0, completion: $0) })
        XCTAssertEqual(try usage(store).records, 0)
        XCTAssertEqual(try usage(store).logicalPayloadBytes, 0)
        XCTAssertFalse(try awaitResult { store.recordMedia(eventKey: "limited", slot: 0, kind: "document", mimeType: "application/octet-stream", data: Data([9]), generation: 0, completion: $0) })
    }

    func testMediaRetryIsIdempotentAndRejectsDifferentBytes() throws {
        let store = try open()
        _ = try write(store, [event("retry")])
        for _ in 0..<2 {
            XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "retry", slot: 0, kind: "voice", mimeType: "audio/ogg", data: Data([1, 2]), generation: 0, completion: $0) })
        }
        assertError(.eventConflict) { try awaitResult { store.recordMedia(eventKey: "retry", slot: 0, kind: "voice", mimeType: "audio/ogg", data: Data([3]), generation: 0, completion: $0) } }
        XCTAssertEqual(try awaitResult { store.media(eventKey: "retry", slot: 0, completion: $0) }?.data, Data([1, 2]))
    }

    private func mediaFile(name: String = "source", chunks: Int = 1) throws -> URL {
        let url = directory.appendingPathComponent(name)
        XCTAssertTrue(FileManager.default.createFile(atPath: url.path, contents: nil))
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        let chunk = Data(repeating: 0x5b, count: 64 * 1024)
        for _ in 0..<chunks { try handle.write(contentsOf: chunk) }
        return url
    }

    private final class MediaWorkerGate {
        private let lock = NSLock()
        private var armed = false
        let entered = DispatchSemaphore(value: 0)
        let released = DispatchSemaphore(value: 0)

        func arm() { lock.lock(); armed = true; lock.unlock() }
        func clock() -> Int64 {
            lock.lock()
            let shouldWait = armed
            armed = false
            lock.unlock()
            if shouldWait {
                entered.signal()
                _ = released.wait(timeout: .now() + 10)
            }
            return 1000
        }
    }

    func testOwnedMediaDescriptorSurvivesUnlinkBeforeQueuedReadAndReopen() throws {
        let gate = MediaWorkerGate()
        let store = try open(clock: { gate.clock() })
        _ = try write(store, [event("owned-unlink")])
        let url = try mediaFile(chunks: 5)
        var owner = try ArchiveMediaFileSource.acquire(source: url, expectedSize: 5 * 64 * 1024)
        let descriptor = try XCTUnwrap(owner).descriptor
        weak var observedOwner = owner
        let done = expectation(description: "owned copy after unlink")
        gate.arm()
        defer { gate.released.signal() }
        store.recordMediaFile(eventKey: "owned-unlink", slot: 0, kind: "video", mimeType: "video/mp4",
                              source: try XCTUnwrap(owner), generation: 0) { result in
            XCTAssertEqual(try? result.get(), true)
            done.fulfill()
        }
        owner = nil // The queued store operation must keep the descriptor alive.
        guard gate.entered.wait(timeout: .now() + 5) == .success else { XCTFail("Worker did not enter gate"); return }
        XCTAssertNotNil(observedOwner)
        XCTAssertGreaterThanOrEqual(fcntl(descriptor, F_GETFD), 0)
        try FileManager.default.removeItem(at: url)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        gate.released.signal()
        wait(for: [done], timeout: 10)
        _ = try usage(store) // A worker barrier after the retained operation ends.
        XCTAssertNil(observedOwner)
        close(store)
        let reopened = try open()
        XCTAssertEqual(try awaitResult { reopened.mediaInfo(eventKey: "owned-unlink", slot: 0, completion: $0) }?.byteCount, 5 * 64 * 1024)
        for chunk in 0..<5 {
            XCTAssertEqual(try awaitResult { reopened.mediaRange(eventKey: "owned-unlink", slot: 0, offset: Int64(chunk * 64 * 1024), count: 64 * 1024, completion: $0) }, Data(repeating: 0x5b, count: 64 * 1024))
        }
    }

    func testOwnedMediaEqualSizePathReplacementUsesOriginalBytesAndRetryOffsets() throws {
        let store = try open()
        _ = try write(store, [event("owned-replace")])
        let url = try mediaFile(chunks: 2)
        let owner = try XCTUnwrap(ArchiveMediaFileSource.acquire(source: url))
        let replacement = directory.appendingPathComponent("replacement")
        XCTAssertTrue(FileManager.default.createFile(atPath: replacement.path, contents: nil))
        let writer = try FileHandle(forWritingTo: replacement)
        for _ in 0..<2 { try writer.write(contentsOf: Data(repeating: 0x44, count: 64 * 1024)) }
        try writer.close()
        XCTAssertEqual(Darwin.rename(replacement.path, url.path), 0)
        // The replacement is complete and the same size, so reopening by path
        // would silently archive the wrong bytes. The acquired descriptor wins.
        for _ in 0..<2 {
            XCTAssertTrue(try awaitResult { store.recordMediaFile(eventKey: "owned-replace", slot: 0, kind: "document", mimeType: "application/octet-stream", source: owner, generation: 0, completion: $0) })
        }
        for chunk in 0..<2 {
            XCTAssertEqual(try awaitResult { store.mediaRange(eventKey: "owned-replace", slot: 0, offset: Int64(chunk * 64 * 1024), count: 64 * 1024, completion: $0) }, Data(repeating: 0x5b, count: 64 * 1024))
        }
        let different = try XCTUnwrap(ArchiveMediaFileSource.acquire(source: url))
        assertError(.eventConflict) { try awaitResult { store.recordMediaFile(eventKey: "owned-replace", slot: 0, kind: "document", mimeType: "application/octet-stream", source: different, generation: 0, completion: $0) } }
    }

    func testURLMediaCompatibilityStillOpensOnWorkerAfterItsClockCheck() throws {
        let gate = MediaWorkerGate()
        let store = try open(clock: { gate.clock() })
        _ = try write(store, [event("url-compatibility")])
        let url = try mediaFile()
        let done = expectation(description: "URL source missing before worker open")
        gate.arm()
        defer { gate.released.signal() }
        store.recordMediaFile(eventKey: "url-compatibility", slot: 0, kind: "document", mimeType: "application/octet-stream", source: url, generation: 0) { result in
            XCTAssertEqual(try? result.get(), false)
            done.fulfill()
        }
        guard gate.entered.wait(timeout: .now() + 5) == .success else { XCTFail("Worker did not enter gate"); return }
        try FileManager.default.removeItem(at: url)
        gate.released.signal()
        wait(for: [done], timeout: 10)
        XCTAssertNil(try awaitResult { store.mediaInfo(eventKey: "url-compatibility", slot: 0, completion: $0) })
    }

    func testOwnedMediaDescriptorFlagsInvalidationAndLastOwnerLifetime() throws {
        _ = try open()
        let url = try mediaFile()
        var owner = try ArchiveMediaFileSource.acquire(source: url)
        var shared = owner
        let descriptor = try XCTUnwrap(owner).descriptor
        XCTAssertNotEqual(fcntl(descriptor, F_GETFD) & FD_CLOEXEC, 0)
        XCTAssertNotEqual(fcntl(descriptor, F_GETFL) & O_NONBLOCK, 0)
        XCTAssertEqual(fcntl(descriptor, F_GETFL) & O_ACCMODE, O_RDONLY)
        owner?.invalidate()
        owner?.invalidate()
        XCTAssertGreaterThanOrEqual(fcntl(descriptor, F_GETFD), 0, "Revocation must not close a descriptor still retained by a reader")
        owner = nil
        XCTAssertGreaterThanOrEqual(fcntl(descriptor, F_GETFD), 0)
        assertError(.protectionUnavailable) { try shared!.stream { _, _, _ in XCTFail("Invalidated owner was read") } }
        shared = nil
        let closedFlags = fcntl(descriptor, F_GETFD)
        let closedError = errno
        XCTAssertEqual(closedFlags, -1)
        XCTAssertEqual(closedError, EBADF)
    }

    func testOwnedMediaAcquisitionRejectsMissingSymlinkNonregularOversizeAndMismatch() throws {
        _ = try open()
        let url = try mediaFile()
        let link = directory.appendingPathComponent("owned-link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: url)
        let fifo = directory.appendingPathComponent("owned-fifo")
        XCTAssertEqual(mkfifo(fifo.path, 0o600), 0)
        for source in [link, fifo, directory!, directory.appendingPathComponent("missing")] {
            XCTAssertNil(try ArchiveMediaFileSource.acquire(source: source))
        }
        XCTAssertNil(try ArchiveMediaFileSource.acquire(source: url, expectedSize: 1))
        assertError(.invalidInput) { try ArchiveMediaFileSource.acquire(source: url, expectedSize: -1) }
        assertError(.invalidInput) { try ArchiveMediaFileSource.acquire(source: URL(string: "https://example.invalid/file")!) }
        let oversized = try mediaFile(name: "owned-oversized", chunks: 0)
        let sparse = try FileHandle(forWritingTo: oversized)
        try sparse.truncate(atOffset: 512 * 1024 * 1024 + 1)
        try sparse.close()
        XCTAssertNil(try ArchiveMediaFileSource.acquire(source: oversized))
    }

    func testOwnedMediaCloseCallbackRunsOnceAfterFinalCloseButNotOnAcquisitionFailure() throws {
        _ = try open()
        let url = try mediaFile()
        var calls = 0
        var descriptor: Int32 = -1
        var callbackObservedClosedDescriptor = false
        var owner = try ArchiveMediaFileSource.acquire(source: url, onClose: {
            calls += 1
            callbackObservedClosedDescriptor = fcntl(descriptor, F_GETFD) == -1 && errno == EBADF
        })
        descriptor = try XCTUnwrap(owner).descriptor
        var retained = owner
        owner?.invalidate()
        owner = nil
        XCTAssertEqual(calls, 0)
        XCTAssertNotNil(retained)
        retained = nil
        XCTAssertEqual(calls, 1)
        XCTAssertTrue(callbackObservedClosedDescriptor)
        XCTAssertNil(try ArchiveMediaFileSource.acquire(source: directory.appendingPathComponent("missing-owner"), onClose: { calls += 1 }))
        XCTAssertNil(try ArchiveMediaFileSource.acquire(source: url, expectedSize: 1, onClose: { calls += 1 }))
        assertError(.invalidInput) { try ArchiveMediaFileSource.acquire(source: url, expectedSize: -1, onClose: { calls += 1 }) }
        XCTAssertEqual(calls, 1, "The caller releases its reservation on nil/error acquisition")
    }

    func testOwnedMediaQuotaStaleGenerationAndClosedStoreKeepExistingData() throws {
        let store = try open(retention: ArchiveRetentionPolicy(maxPayloadBytes: 1024))
        _ = try write(store, [event("owned-policy")])
        let owner = try XCTUnwrap(ArchiveMediaFileSource.acquire(source: mediaFile()))
        let before = try usage(store)
        XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "owned-policy", slot: 0, kind: "document", mimeType: "application/octet-stream", source: owner, generation: 0, completion: $0) })
        XCTAssertEqual(try usage(store), before)
        XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "owned-policy", slot: 0, kind: "document", mimeType: "application/octet-stream", source: owner, expectedSize: 1, generation: 0, completion: $0) })
        _ = try purge(store, to: 1)
        assertError(.generationMismatch(expected: 1, received: 0)) { try awaitResult { store.recordMediaFile(eventKey: "owned-policy", slot: 0, kind: "document", mimeType: "application/octet-stream", source: owner, generation: 0, completion: $0) } }
        close(store)
        assertError(.closed) { try awaitResult { store.recordMediaFile(eventKey: "owned-policy", slot: 0, kind: "document", mimeType: "application/octet-stream", source: owner, generation: 1, completion: $0) } }
        XCTAssertGreaterThanOrEqual(fcntl(owner.descriptor, F_GETFD), 0, "Store rejection must not close the caller's owner")
    }

    func testOwnedMediaInvalidatedWhileQueuedDoesNotWriteAttachmentOrUsage() throws {
        let gate = MediaWorkerGate()
        let store = try open(clock: { gate.clock() })
        _ = try write(store, [event("owned-revoked")])
        let owner = try XCTUnwrap(ArchiveMediaFileSource.acquire(source: mediaFile(chunks: 3)))
        let before = try usage(store)
        let done = expectation(description: "revoked queued copy")
        gate.arm()
        defer { gate.released.signal() }
        store.recordMediaFile(eventKey: "owned-revoked", slot: 0, kind: "video", mimeType: "video/mp4", source: owner, generation: 0) { result in
            if case let .failure(error) = result { XCTAssertEqual(error, .protectionUnavailable) }
            else { XCTFail("A revoked queued source was accepted") }
            done.fulfill()
        }
        guard gate.entered.wait(timeout: .now() + 5) == .success else { XCTFail("Worker did not enter gate"); return }
        owner.invalidate()
        gate.released.signal()
        wait(for: [done], timeout: 10)
        XCTAssertEqual(try usage(store), before)
        XCTAssertNil(try awaitResult { store.mediaInfo(eventKey: "owned-revoked", slot: 0, completion: $0) })
    }

    func testOwnedMediaCancellationBetweenChunksAndMetadataMutationAreRejected() throws {
        let store = try open()
        _ = try write(store, [event("owned-mutation")])
        let url = try mediaFile(chunks: 3)
        let revoked = try XCTUnwrap(ArchiveMediaFileSource.acquire(source: url))
        var consumed = 0
        assertError(.protectionUnavailable) {
            try revoked.stream { _, count, _ in consumed += count; revoked.invalidate() }
        }
        XCTAssertEqual(consumed, 64 * 1024)
        let changed = try XCTUnwrap(ArchiveMediaFileSource.acquire(source: url))
        let before = try usage(store)
        let writer = try FileHandle(forWritingTo: url)
        try writer.truncate(atOffset: 1)
        try writer.close()
        assertError(.protectionUnavailable) { try awaitResult { store.recordMediaFile(eventKey: "owned-mutation", slot: 0, kind: "document", mimeType: "application/octet-stream", source: changed, generation: 0, completion: $0) } }
        XCTAssertEqual(try usage(store), before)
        XCTAssertNil(try awaitResult { store.mediaInfo(eventKey: "owned-mutation", slot: 0, completion: $0) })
    }

    func testOwnedMediaDetectsSameSizeModificationDuringStream() throws {
        _ = try open()
        let url = try mediaFile(chunks: 3)
        let owner = try XCTUnwrap(ArchiveMediaFileSource.acquire(source: url))
        var initial = stat()
        XCTAssertEqual(fstat(owner.descriptor, &initial), 0)
        let writer = try FileHandle(forWritingTo: url)
        defer { try? writer.close() }
        assertError(.protectionUnavailable) {
            try owner.stream { _, _, offset in
                if offset == 0 {
                    try writer.seek(toOffset: 64 * 1024)
                    try writer.write(contentsOf: Data([0x44]))
                    // Make timestamp change deterministic even on coarse or fast filesystems.
                    var times = [initial.st_atimespec, timespec(tv_sec: initial.st_mtimespec.tv_sec + 1, tv_nsec: initial.st_mtimespec.tv_nsec)]
                    XCTAssertEqual(times.withUnsafeMutableBufferPointer { futimens(writer.fileDescriptor, $0.baseAddress) }, 0)
                }
            }
        }
        var after = stat()
        XCTAssertEqual(fstat(owner.descriptor, &after), 0)
        XCTAssertEqual(after.st_size, initial.st_size)
    }

    func testStreamedLargeMediaSurvivesSourceRemovalAndReopenWithBoundedReads() throws {
        let store = try open()
        _ = try write(store, [event("large")])
        let source = try mediaFile(chunks: 320) // 20 MiB, never allocated as one buffer.
        XCTAssertTrue(try awaitResult { store.recordMediaFile(eventKey: "large", slot: 0, kind: "video", mimeType: "video/mp4", source: source, expectedSize: 20 * 1024 * 1024, generation: 0, completion: $0) })
        XCTAssertTrue(try awaitResult { store.recordMediaFile(eventKey: "large", slot: 0, kind: "video", mimeType: "video/mp4", source: source, generation: 0, completion: $0) })
        try FileManager.default.removeItem(at: source)
        close(store)
        let reopened = try open()
        let info = try XCTUnwrap(try awaitResult { reopened.mediaInfo(eventKey: "large", slot: 0, completion: $0) })
        XCTAssertEqual(info.byteCount, 20 * 1024 * 1024)
        XCTAssertEqual(info.kind, "video")
        let offsets: [Int64] = [0, 64 * 1024 - 1, 17 * 1024 * 1024, 20 * 1024 * 1024 - 7]
        for offset in offsets {
            let bytes = try XCTUnwrap(try awaitResult { reopened.mediaRange(eventKey: "large", slot: 0, offset: offset, count: 64 * 1024, completion: $0) })
            let expectedCount = min(64 * 1024, 20 * 1024 * 1024 - Int(offset))
            XCTAssertEqual(bytes, Data(repeating: 0x5b, count: expectedCount))
        }
        XCTAssertEqual(try awaitResult { reopened.mediaRange(eventKey: "large", slot: 0, offset: 20 * 1024 * 1024, count: 10, completion: $0) }, Data())
        assertError(.invalidInput) { try awaitResult { reopened.media(eventKey: "large", slot: 0, completion: $0) } }
        assertError(.invalidInput) { try awaitResult { reopened.mediaRange(eventKey: "large", slot: 0, offset: -1, count: 10, completion: $0) } }
        assertError(.invalidInput) { try awaitResult { reopened.mediaRange(eventKey: "large", slot: 0, offset: 0, count: 1024 * 1024 + 1, completion: $0) } }
        _ = try purge(reopened, to: 1)
        XCTAssertNil(try awaitResult { reopened.mediaInfo(eventKey: "large", slot: 0, completion: $0) })
        XCTAssertNil(try awaitResult { reopened.mediaRange(eventKey: "large", slot: 0, offset: 0, count: 10, completion: $0) })
        XCTAssertEqual(try usage(reopened).logicalPayloadBytes, 0)
    }

    func testStreamedMediaRejectsIncompleteUnsafeOversizeAndConflictingSources() throws {
        let directory = try XCTUnwrap(self.directory)
        let store = try open()
        _ = try write(store, [event("file")])
        let source = try mediaFile()
        let link = directory.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: source)
        for candidate in [link, directory, directory.appendingPathComponent("absent")] {
            XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "file", slot: 0, kind: "document", mimeType: "application/octet-stream", source: candidate, generation: 0, completion: $0) })
        }
        XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "file", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, expectedSize: 1, generation: 0, completion: $0) })
        XCTAssertTrue(try awaitResult { store.recordMediaFile(eventKey: "file", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) })
        let writer = try FileHandle(forWritingTo: source)
        try writer.write(contentsOf: Data([0]))
        try writer.close()
        assertError(.eventConflict) { try awaitResult { store.recordMediaFile(eventKey: "file", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) } }
        let oversized = try mediaFile(name: "oversized", chunks: 0)
        let sparse = try FileHandle(forWritingTo: oversized)
        try sparse.truncate(atOffset: 512 * 1024 * 1024 + 1)
        try sparse.close()
        XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "file", slot: 1, kind: "document", mimeType: "application/octet-stream", source: oversized, generation: 0, completion: $0) })
        XCTAssertEqual(try awaitResult { store.mediaRange(eventKey: "file", slot: 0, offset: 0, count: 1, completion: $0) }, Data([0x5b]))
    }

    func testStreamedMediaFailureRollsBackZeroBlobAndUsageBeforeRetry() throws {
        let store = try open()
        _ = try write(store, [event("rollback")])
        let source = try mediaFile()
        let originalUsage = try usage(store).logicalPayloadBytes
        // SQLite forbids incremental BLOB writes to an indexed column. Force a
        // failure after INSERT but before copying, then remove the obstruction.
        try rawDatabase(store.databaseURL, statements: ["CREATE INDEX reject_incremental ON event_media(payload)"])
        XCTAssertThrowsError(try awaitResult { store.recordMediaFile(eventKey: "rollback", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) })
        XCTAssertEqual(try usage(store).logicalPayloadBytes, originalUsage)
        XCTAssertNil(try awaitResult { store.mediaInfo(eventKey: "rollback", slot: 0, completion: $0) })
        try rawDatabase(store.databaseURL, statements: ["DROP INDEX reject_incremental"])
        XCTAssertTrue(try awaitResult { store.recordMediaFile(eventKey: "rollback", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) })
        XCTAssertEqual(try usage(store).logicalPayloadBytes, originalUsage + 64 * 1024 + 64 + 8 + 24)
    }

    func testStreamedMediaQuotaAndAccountGenerationAreEnforced() throws {
        let store = try open(retention: ArchiveRetentionPolicy(maxPayloadBytes: 1024))
        let other = try open(account: 2, keys: Keys(0x74))
        _ = try write(store, [event("scoped")])
        let source = try mediaFile()
        XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "scoped", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) })
        XCTAssertEqual(try records(store).count, 1) // Keep text when the file cannot fit.
        XCTAssertNil(try awaitResult { other.mediaInfo(eventKey: "scoped", slot: 0, completion: $0) })
        _ = try purge(store, to: 1)
        assertError(.generationMismatch(expected: 1, received: 0)) { try awaitResult { store.recordMediaFile(eventKey: "scoped", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) } }
    }

    func testStreamedQuotaPreservesTextAndEarlierAttachmentsWhenWholeSnapshotCannotFit() throws {
        let store = try open(retention: ArchiveRetentionPolicy(maxPayloadBytes: 64 * 1024 + 96))
        _ = try write(store, [event("boundary")])
        let source = try mediaFile()
        // The file alone fits exactly; file + parent text does not.
        XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "boundary", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) })
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["boundary"])
        XCTAssertEqual(try records(store).first?.storedMediaCount, 0)
        _ = try awaitResult { store.setRetention(ArchiveRetentionPolicy(maxPayloadBytes: 100_000), completion: $0) }
        XCTAssertTrue(try awaitResult { store.recordMediaFile(eventKey: "boundary", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) })
        XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "boundary", slot: 1, kind: "document", mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) })
        XCTAssertEqual(try records(store).first?.storedMediaCount, 1)
        XCTAssertEqual(try awaitResult { store.mediaRange(eventKey: "boundary", slot: 0, offset: 0, count: 1, completion: $0) }, Data([0x5b]))
        XCTAssertLessThanOrEqual(try usage(store).logicalPayloadBytes, 100_000)
    }

    func testEmptyDocumentIsValidButEmptyMediaIsNotReportedAsPreserved() throws {
        let store = try open()
        _ = try write(store, [event("empty")])
        let source = try mediaFile(chunks: 0)
        for kind in ["photo", "video", "videoMessage", "voice", "animation"] {
            XCTAssertFalse(try awaitResult { store.recordMediaFile(eventKey: "empty", slot: 0, kind: kind, mimeType: "application/octet-stream", source: source, generation: 0, completion: $0) })
        }
        XCTAssertEqual(try records(store).first?.storedMediaCount, 0)
        XCTAssertTrue(try awaitResult { store.recordMediaFile(eventKey: "empty", slot: 0, kind: "document", mimeType: "application/octet-stream", source: source, expectedSize: 0, generation: 0, completion: $0) })
        XCTAssertEqual(try awaitResult { store.mediaInfo(eventKey: "empty", slot: 0, completion: $0) }?.byteCount, 0)
        XCTAssertEqual(try awaitResult { store.mediaRange(eventKey: "empty", slot: 0, offset: 0, count: 32, completion: $0) }, Data())
    }

    func testGalleryContainsOnlyRetainedSlotsAndPagesWithoutDuplicates() throws {
        let store = try open()
        _ = try write(store, [event("first", text: "first caption", peer: 10), event("metadata-only", peer: 10), event("second", text: "second caption", peer: 20, kind: .edited)])
        for slot in 0..<2 {
            XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "first", slot: slot, kind: "photo", mimeType: "image/jpeg", data: Data([UInt8(slot + 1)]), generation: 0, completion: $0) })
        }
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "second", slot: 0, kind: "video", mimeType: "video/mp4", data: Data([3]), generation: 0, completion: $0) })
        let page = try awaitResult { store.mediaItems(limit: 2, completion: $0) }
        XCTAssertEqual(page.map(\.eventKey), ["second", "first"])
        XCTAssertEqual(page.map(\.slot), [0, 1])
        let cursor = try XCTUnwrap(page.last)
        let next = try awaitResult { store.mediaItems(beforeSequence: cursor.sequence, beforeSlot: cursor.slot, limit: 2, completion: $0) }
        XCTAssertEqual(next.map(\.eventKey), ["first"])
        XCTAssertEqual(next.map(\.slot), [0])
        XCTAssertEqual(try awaitResult { store.mediaItems(peerId: 10, completion: $0) }.count, 2)
        let videos = try awaitResult { store.mediaItems(kind: "video", completion: $0) }
        XCTAssertEqual(videos.first?.eventKind, .edited)
        XCTAssertEqual(videos.first?.byteCount, 1)
        XCTAssertEqual(videos.first?.caption, "second caption")
        let other = try open(account: 2, keys: Keys(0x75))
        XCTAssertTrue(try awaitResult { other.mediaItems(completion: $0) }.isEmpty)
        close(store)
        let reopened = try open()
        XCTAssertEqual(try awaitResult { reopened.mediaItems(completion: $0) }.count, 3)
        _ = try purge(reopened, to: 1)
        XCTAssertTrue(try awaitResult { reopened.mediaItems(completion: $0) }.isEmpty)
    }

    func testGalleryMigrationBackfillsExistingMediaAndRejectsInvalidCursors() throws {
        let store = try open()
        _ = try write(store, [event("legacy-gallery")])
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "legacy-gallery", slot: 3, kind: "voice", mimeType: "audio/ogg", data: Data([5]), generation: 0, completion: $0) })
        let url = store.databaseURL
        close(store)
        try rawDatabase(url, statements: ["DROP TRIGGER archive_gallery_insert", "DROP TRIGGER archive_gallery_delete", "DROP TABLE media_gallery", "PRAGMA user_version=7"])
        let reopened = try open()
        let entries = try awaitResult { reopened.mediaItems(completion: $0) }
        XCTAssertEqual(entries.first?.slot, 3)
        XCTAssertEqual(entries.first?.kind, "voice")
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 8)
        assertError(.invalidInput) { try awaitResult { reopened.mediaItems(beforeSequence: 1, completion: $0) } }
        assertError(.invalidInput) { try awaitResult { reopened.mediaItems(beforeSlot: 1, completion: $0) } }
        assertError(.invalidInput) { try awaitResult { reopened.mediaItems(kind: "bad\0type", completion: $0) } }
    }

    func testGalleryMigrationFailureRollsBackAndCanRecover() throws {
        let store = try open()
        _ = try write(store, [event("rollback-gallery")])
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "rollback-gallery", slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1, 2]), generation: 0, completion: $0) })
        let url = store.databaseURL
        close(store)
        try rawDatabase(url, statements: ["DROP TRIGGER archive_gallery_insert", "DROP TRIGGER archive_gallery_delete", "DROP TABLE media_gallery", "CREATE TABLE media_gallery(unexpected INTEGER)", "PRAGMA user_version=7"])
        XCTAssertThrowsError(try open())
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 7)
        XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM event_media"), 1)
        try rawDatabase(url, statements: ["DROP TABLE media_gallery"])
        let recovered = try open()
        XCTAssertEqual(try awaitResult { recovered.mediaItems(completion: $0) }.count, 1)
        XCTAssertEqual(try awaitResult { recovered.media(eventKey: "rollback-gallery", slot: 0, completion: $0) }?.data, Data([1, 2]))
    }

    func testGalleryRetentionAndFailedMediaWriteLeaveNoStaleEntries() throws {
        let store = try open()
        _ = try write(store, [event("old-gallery", observed: 900), event("new-gallery", observed: 990)])
        for key in ["old-gallery", "new-gallery"] {
            XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: key, slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1]), generation: 0, completion: $0) })
        }
        assertError(.eventConflict) {
            try awaitResult { store.recordMedia(eventKey: "new-gallery", slot: 0, kind: "video", mimeType: "video/mp4", data: Data([2]), generation: 0, completion: $0) }
        }
        _ = try awaitResult { store.setRetention(ArchiveRetentionPolicy(maxAgeSeconds: 50), completion: $0) }
        let entries = try awaitResult { store.mediaItems(completion: $0) }
        XCTAssertEqual(entries.map(\.eventKey), ["new-gallery"])
        XCTAssertEqual(entries.first?.kind, "photo")
        XCTAssertEqual(try rawInteger(store.databaseURL, query: "SELECT COUNT(*) FROM media_gallery"), 1)
        XCTAssertEqual(try rawInteger(store.databaseURL, query: "SELECT COUNT(*) FROM media_gallery g LEFT JOIN event_media m ON m.event_key=g.event_key AND m.slot=g.slot WHERE m.event_key IS NULL"), 0)
    }

    func testVersionSixMediaMigrationPreservesBytesCountersAndCascade() throws {
        let store = try open()
        _ = try write(store, [event("migrate-media")])
        XCTAssertTrue(try awaitResult { store.recordMedia(eventKey: "migrate-media", slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1, 2, 3]), generation: 0, completion: $0) })
        let originalUsage = try usage(store).logicalPayloadBytes
        let url = store.databaseURL
        close(store)
        try rawDatabase(url, statements: ["DROP TRIGGER archive_gallery_insert", "DROP TRIGGER archive_gallery_delete", "DROP TABLE media_gallery", "PRAGMA user_version=6"])
        let reopened = try open()
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 8)
        XCTAssertEqual(try usage(reopened).logicalPayloadBytes, originalUsage)
        XCTAssertEqual(try awaitResult { reopened.media(eventKey: "migrate-media", slot: 0, completion: $0) }?.data, Data([1, 2, 3]))
        _ = try purge(reopened, to: 1)
        XCTAssertEqual(try usage(reopened).logicalPayloadBytes, 0)
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
        try write(store, events, generation: 0)
    }
    private func write(_ store: ArchiveStore, _ events: [ArchiveEvent], generation: Int64) throws -> ArchiveWriteResult {
        try awaitResult { store.record(events.map { capture($0) }, generation: generation, policy: enabled, completion: $0) }
    }
    private func generation(_ store: ArchiveStore) throws -> Int64 {
        try awaitResult { store.currentGeneration(completion: $0) }
    }
    private func writeOutbox(_ store: ArchiveStore, _ event: ArchiveEvent, index: Int64,
                             generation: Int64 = 0, policy: ArchiveCapturePolicy? = nil) throws -> ArchiveWriteResult {
        try awaitResult {
            store.recordFromOutbox(capture(event), index: index, generation: generation,
                                   policy: policy ?? enabled, completion: $0)
        }
    }
    private func purge(_ store: ArchiveStore, to target: Int64) throws -> ArchivePurgeResult {
        try awaitResult { store.purge(advancingToGeneration: target, completion: $0) }
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
            store.record([capture(event("disabled"))], generation: 0, policy: ArchiveCapturePolicy(), completion: $0)
        }
        XCTAssertEqual(defaultResult.excluded, 1)
        let contexts = [ArchiveCaptureContext(), ArchiveCaptureContext(ordinaryCloudMessage: true, secretChat: true),
                        ArchiveCaptureContext(ordinaryCloudMessage: true, expiring: true),
                        ArchiveCaptureContext(ordinaryCloudMessage: true, viewOnce: true),
                        ArchiveCaptureContext(ordinaryCloudMessage: true, botConversation: true)]
        let result: ArchiveWriteResult = try awaitResult {
            store.record(contexts.enumerated().map { capture(event("excluded-\($0.offset)"), $0.element) }, generation: 0, policy: enabled, completion: $0)
        }
        XCTAssertEqual(result.excluded, 5)
        let excluded = ArchiveCapturePolicy(enabled: true, excludedPeers: [10], excludedSenders: [20])
        let peerSender: ArchiveWriteResult = try awaitResult {
            store.record([capture(event("peer", peer: 10)), capture(event("sender", author: 20))], generation: 0, policy: excluded, completion: $0)
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
        _ = try purge(store, to: 1)
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
        try rawDatabase(store.databaseURL, statements: ["PRAGMA user_version=9"])
        let before = try Data(contentsOf: store.databaseURL)
        assertError(.unsupportedSchema(9)) { try open() }
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
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 8)
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
        _ = try purge(first, to: 1)
        XCTAssertEqual(try usage(second), ArchiveUsage(records: 0, logicalPayloadBytes: 0))
        _ = try write(second, [event("after-purge")], generation: 1)
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

    private func legacyV2Database() throws -> URL {
        let url = try legacyV1Database()
        try rawDatabase(url, statements: [
            """
            CREATE TABLE archive_usage(id INTEGER PRIMARY KEY CHECK(id=1),
            record_count INTEGER NOT NULL CHECK(typeof(record_count)='integer' AND record_count>=0),
            payload_bytes INTEGER NOT NULL CHECK(typeof(payload_bytes)='integer' AND payload_bytes>=0))
            """,
            "INSERT INTO archive_usage SELECT 1,COUNT(*),COALESCE(SUM(payload_bytes),0) FROM events",
            """
            CREATE TRIGGER archive_events_validate_insert BEFORE INSERT ON events
            WHEN typeof(NEW.payload_bytes)!='integer' OR NEW.payload_bytes<0
            OR NEW.payload_bytes!=64+length(CAST(NEW.text AS BLOB))+length(NEW.entities)+length(CAST(NEW.event_key AS BLOB))
            BEGIN SELECT RAISE(ABORT,'Invalid archive payload size'); END
            """,
            """
            CREATE TRIGGER archive_events_insert_usage AFTER INSERT ON events BEGIN
            UPDATE archive_usage SET record_count=record_count+1,payload_bytes=payload_bytes+NEW.payload_bytes WHERE id=1; END
            """,
            """
            CREATE TRIGGER archive_events_delete_usage AFTER DELETE ON events BEGIN
            UPDATE archive_usage SET record_count=record_count-1,payload_bytes=payload_bytes-OLD.payload_bytes WHERE id=1; END
            """,
            "CREATE TRIGGER archive_events_immutable BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'Archive events are immutable'); END",
            "PRAGMA user_version=2"
        ])
        return url
    }

    func testVersionTwoMigrationPreservesHistoryCountersAndInitializesGeneration() throws {
        let url = try legacyV2Database()
        let store = try open()
        XCTAssertEqual(try generation(store), 0)
        XCTAssertEqual(try usage(store), ArchiveUsage(records: 2, logicalPayloadBytes: 167))
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 8)
        XCTAssertEqual(try records(store).first?.event.eventKey, "legacy-delete")
        _ = try write(store, [event("generation-zero")], generation: 0)
        close(store)
        let reopened = try open()
        XCTAssertEqual(try generation(reopened), 0)
        XCTAssertEqual(try usage(reopened).records, 3)
    }

    func testRecordRejectsStaleFutureAndNegativeGenerationWithoutMutation() throws {
        let store = try open()
        assertError(.generationMismatch(expected: 0, received: 1)) { try write(store, [event("future")], generation: 1) }
        assertError(.invalidInput) { try write(store, [event("negative")], generation: -1) }
        _ = try write(store, [event("old")], generation: 0)
        _ = try purge(store, to: 1)
        assertError(.generationMismatch(expected: 1, received: 0)) { try write(store, [event("stale")], generation: 0) }
        assertError(.generationMismatch(expected: 1, received: 2)) { try write(store, [event("future")], generation: 2) }
        XCTAssertEqual(try usage(store).records, 0)
        _ = try write(store, [event("current")], generation: 1)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["current"])
    }

    func testSameGenerationPurgeRetryPreservesNewWritesAndRejectsGaps() throws {
        let store = try open()
        _ = try write(store, [event("initial")])
        XCTAssertEqual(try purge(store, to: 0), ArchivePurgeResult(generation: 0, didPurge: false))
        XCTAssertEqual(try usage(store).records, 1)
        XCTAssertEqual(try purge(store, to: 1), ArchivePurgeResult(generation: 1, didPurge: true))
        _ = try write(store, [event("after-purge")], generation: 1)
        XCTAssertEqual(try purge(store, to: 1), ArchivePurgeResult(generation: 1, didPurge: false))
        assertError(.generationMismatch(expected: 2, received: 0)) { try purge(store, to: 0) }
        assertError(.generationMismatch(expected: 2, received: 3)) { try purge(store, to: 3) }
        assertError(.invalidInput) { try purge(store, to: -1) }
        XCTAssertEqual(try generation(store), 1)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["after-purge"])
    }

    func testGenerationPersistsAcrossReopenAndAlreadyOpenHandlesAndAccounts() throws {
        let first = try open(), second = try open(), other = try open(account: 2)
        _ = try write(first, [event("first")])
        _ = try write(other, [event("other", account: 2)])
        _ = try purge(second, to: 1)
        XCTAssertEqual(try generation(first), 1)
        assertError(.generationMismatch(expected: 1, received: 0)) { try write(first, [event("pending-stale")], generation: 0) }
        _ = try write(first, [event("accepted")], generation: 1)
        XCTAssertEqual(try generation(other), 0)
        XCTAssertEqual(try usage(other).records, 1)
        close(first); close(second)
        let reopened = try open()
        XCTAssertEqual(try generation(reopened), 1)
        XCTAssertEqual(try records(reopened).map(\.event.eventKey), ["accepted"])
        XCTAssertFalse(try purge(reopened, to: 1).didPurge)
        XCTAssertEqual(try usage(reopened).records, 1)
    }

    func testPurgeFailureRollsBackDeletedRowsCountersAndGeneration() throws {
        let store = try open()
        _ = try write(store, [event("one"), event("two")])
        let before = try usage(store)
        try rawDatabase(store.databaseURL, statements: [
            """
            CREATE TRIGGER test_generation_fault BEFORE UPDATE OF capture_generation ON archive_meta
            BEGIN SELECT RAISE(ABORT,'Injected generation update failure'); END
            """
        ])
        XCTAssertThrowsError(try purge(store, to: 1))
        XCTAssertEqual(try generation(store), 0)
        XCTAssertEqual(try usage(store), before)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["two", "one"])
        try rawDatabase(store.databaseURL, statements: ["DROP TRIGGER test_generation_fault"])
        XCTAssertTrue(try purge(store, to: 1).didPurge)
        XCTAssertEqual(try usage(store).records, 0)
        XCTAssertEqual(try generation(store), 1)
    }

    func testGenerationExhaustionFailsClosedWithoutIntegerOverflow() throws {
        let first = try open(); close(first)
        try rawDatabase(first.databaseURL, statements: ["UPDATE archive_meta SET capture_generation=\(Int64.max - 1)"])
        let store = try open()
        _ = try write(store, [event("before-max")], generation: Int64.max - 1)
        XCTAssertTrue(try purge(store, to: Int64.max).didPurge)
        _ = try write(store, [event("at-max")], generation: Int64.max)
        XCTAssertFalse(try purge(store, to: Int64.max).didPurge)
        assertError(.invalidInput) { try purge(store, to: Int64.max - 1) }
        XCTAssertEqual(try generation(store), Int64.max)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["at-max"])
    }

    func testQueuedWriterPurgeBoundaryRejectsOldCaptureAndAcceptsNewCapture() throws {
        let store = try open()
        let done = expectation(description: "queued generation boundary")
        done.expectedFulfillmentCount = 4
        store.record([capture(event("before"))], generation: 0, policy: enabled) { result in
            XCTAssertEqual(try? result.get().inserted, 1); done.fulfill()
        }
        store.purge(advancingToGeneration: 1) { result in
            XCTAssertEqual(try? result.get(), ArchivePurgeResult(generation: 1, didPurge: true)); done.fulfill()
        }
        store.record([capture(event("old-pending"))], generation: 0, policy: enabled) { result in
            if case let .failure(error) = result { XCTAssertEqual(error, .generationMismatch(expected: 1, received: 0)) }
            else { XCTFail("Old capture crossed the purge boundary") }
            done.fulfill()
        }
        store.record([capture(event("after"))], generation: 1, policy: enabled) { result in
            XCTAssertEqual(try? result.get().inserted, 1); done.fulfill()
        }
        wait(for: [done], timeout: 10)
        XCTAssertEqual(try generation(store), 1)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["after"])
        XCTAssertEqual(try usage(store).records, 1)
    }

    private func legacyV3Database(extraStatements: [String] = []) throws -> URL {
        let url = try legacyV2Database()
        try rawDatabase(url, statements: [
            "ALTER TABLE archive_meta ADD COLUMN capture_generation INTEGER NOT NULL DEFAULT 0 CHECK(typeof(capture_generation)='integer' AND capture_generation>=0)",
            "PRAGMA user_version=3"
        ] + extraStatements)
        return url
    }

    func testVersionThreeMigrationPreservesGenerationAndInitializesOutboxReceipt() throws {
        let url = try legacyV3Database(extraStatements: ["UPDATE archive_meta SET capture_generation=7"])
        let store = try open()
        XCTAssertEqual(try generation(store), 7)
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 8)
        XCTAssertEqual(try rawInteger(url, query: "SELECT outbox_high_water FROM archive_meta"), -1)
        XCTAssertEqual(try usage(store), ArchiveUsage(records: 2, logicalPayloadBytes: 167))
        XCTAssertEqual(try writeOutbox(store, event("new-receipt"), index: 24, generation: 7).inserted, 1)
        close(store)
        let reopened = try open()
        XCTAssertEqual(try generation(reopened), 7)
        XCTAssertEqual(try rawInteger(url, query: "SELECT outbox_high_water FROM archive_meta"), 24)
        XCTAssertEqual(try writeOutbox(reopened, event("new-receipt"), index: 24, generation: 7).duplicates, 1)
    }

    func testVersionFourMigrationFailureRollsBackReceiptColumns() throws {
        let url = try legacyV3Database(extraStatements: [
            // Second ALTER fails after the first succeeds; the migration must roll back both.
            "ALTER TABLE archive_meta ADD COLUMN outbox_last_event_key TEXT NOT NULL DEFAULT ''"
        ])
        XCTAssertThrowsError(try open())
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 3)
        XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM pragma_table_info('archive_meta') WHERE name='outbox_high_water'"), 0)
        XCTAssertEqual(try rawInteger(url, query: "SELECT COUNT(*) FROM events"), 2)
        try rawDatabase(url, statements: ["ALTER TABLE archive_meta DROP COLUMN outbox_last_event_key"])
        XCTAssertEqual(try usage(open()).records, 2)
        XCTAssertEqual(try rawInteger(url, query: "PRAGMA user_version"), 8)
    }

    func testOutboxReplayAfterQuotaAndAgeRetentionDoesNotResurrectMessages() throws {
        let policy = ArchiveRetentionPolicy(maxAgeSeconds: 1000, maxPayloadBytes: 66)
        let store = try open(retention: policy)
        let first = event("a", text: "a"), second = event("b", text: "b")
        XCTAssertEqual(try writeOutbox(store, first, index: 0).inserted, 1)
        XCTAssertEqual(try writeOutbox(store, second, index: 1).removedByRetention, 1)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["b"])
        // Simulate restart after archive commit but before the Postbox acknowledgement.
        close(store)
        let reopened = try open(retention: policy)
        XCTAssertEqual(try writeOutbox(reopened, first, index: 0).duplicates, 1)
        XCTAssertEqual(try records(reopened).map(\.event.eventKey), ["b"])
        let removed: Int = try awaitResult {
            reopened.setRetention(ArchiveRetentionPolicy(maxAgeSeconds: 50, maxPayloadBytes: 66), completion: $0)
        }
        XCTAssertEqual(removed, 1)
        XCTAssertEqual(try writeOutbox(reopened, second, index: 1).duplicates, 1)
        XCTAssertTrue(try records(reopened).isEmpty)
        XCTAssertEqual(try rawInteger(reopened.databaseURL, query: "SELECT outbox_high_water FROM archive_meta"), 1)
    }

    func testOutboxReceiptFailureRollsBackInsertRetentionCountersAndCursor() throws {
        let store = try open(retention: ArchiveRetentionPolicy(maxAgeSeconds: 1000, maxPayloadBytes: 66))
        _ = try writeOutbox(store, event("a", text: "a"), index: 0)
        try rawDatabase(store.databaseURL, statements: [
            "CREATE TRIGGER test_receipt_fault BEFORE UPDATE OF outbox_high_water ON archive_meta BEGIN SELECT RAISE(ABORT,'Injected receipt failure'); END"
        ])
        XCTAssertThrowsError(try writeOutbox(store, event("b", text: "b"), index: 1))
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["a"])
        XCTAssertEqual(try usage(store), ArchiveUsage(records: 1, logicalPayloadBytes: 66))
        XCTAssertEqual(try rawInteger(store.databaseURL, query: "SELECT outbox_high_water FROM archive_meta"), 0)
        try rawDatabase(store.databaseURL, statements: ["DROP TRIGGER test_receipt_fault"])
        XCTAssertEqual(try writeOutbox(store, event("b", text: "b"), index: 1).inserted, 1)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["b"])
        XCTAssertEqual(try rawInteger(store.databaseURL, query: "SELECT outbox_high_water FROM archive_meta"), 1)
    }

    func testOutboxExclusionsAreConsumedAndDoNotReappearAfterPolicyChange() throws {
        let store = try open()
        let excluded = event("excluded")
        XCTAssertEqual(try writeOutbox(store, excluded, index: 3, policy: ArchiveCapturePolicy()).excluded, 1)
        XCTAssertEqual(try writeOutbox(store, excluded, index: 3).duplicates, 1)
        XCTAssertTrue(try records(store).isEmpty)
        XCTAssertEqual(try writeOutbox(store, event("next"), index: 8).inserted, 1)
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["next"])
    }

    func testOutboxReceiptIsSharedAcrossHandlesButIsolatedAcrossAccountsAndGenerations() throws {
        let first = try open(), second = try open(), other = try open(account: 2)
        let original = event("same")
        _ = try writeOutbox(first, original, index: 20)
        XCTAssertEqual(try writeOutbox(second, original, index: 20).duplicates, 1)
        XCTAssertEqual(try writeOutbox(other, event("same", account: 2), index: 20).inserted, 1)
        _ = try purge(second, to: 1)
        assertError(.generationMismatch(expected: 1, received: 0)) { try writeOutbox(first, original, index: 20) }
        XCTAssertEqual(try writeOutbox(first, event("new-generation"), index: 21, generation: 1).inserted, 1)
        XCTAssertFalse(try purge(second, to: 1).didPurge)
        XCTAssertEqual(try writeOutbox(second, event("new-generation"), index: 21, generation: 1).duplicates, 1)
        XCTAssertEqual(try rawInteger(first.databaseURL, query: "SELECT outbox_high_water FROM archive_meta"), 21)
        XCTAssertEqual(try generation(other), 0)
        XCTAssertEqual(try records(other).map(\.event.eventKey), ["same"])
    }

    func testOutboxRejectsInvalidIndicesAccountGenerationAndBoundaryReuse() throws {
        let store = try open()
        assertError(.invalidInput) { try writeOutbox(store, event("negative"), index: -1) }
        assertError(.accountMismatch) { try writeOutbox(store, event("wrong", account: 2), index: 0) }
        assertError(.generationMismatch(expected: 0, received: 1)) { try writeOutbox(store, event("future"), index: 0, generation: 1) }
        XCTAssertEqual(try rawInteger(store.databaseURL, query: "SELECT outbox_high_water FROM archive_meta"), -1)
        _ = try writeOutbox(store, event("initial"), index: 0)
        assertError(.eventConflict) { try writeOutbox(store, event("reused-index"), index: 0) }
        assertError(.eventConflict) { try writeOutbox(store, event("initial", text: "changed"), index: 0) }
        XCTAssertEqual(try records(store).map(\.event.eventKey), ["initial"])
        XCTAssertEqual(try rawInteger(store.databaseURL, query: "SELECT outbox_high_water FROM archive_meta"), 0)
    }

    func testOutboxMalformedPersistedReceiptFailsClosed() throws {
        let store = try open(); close(store)
        try rawDatabase(store.databaseURL, statements: [
            "PRAGMA ignore_check_constraints=ON",
            "UPDATE archive_meta SET outbox_high_water=-2"
        ])
        assertError(.unavailable(SQLITE_CORRUPT)) { try open() }
        try rawDatabase(store.databaseURL, statements: ["UPDATE archive_meta SET outbox_high_water=1,outbox_last_event_key=''"])
        assertError(.unavailable(SQLITE_CORRUPT)) { try open() }
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
    func testExplicitExtendedPolicyKeepsBothSourceAndExpiryOptInsIndependent() throws {
        let store = try self.open()
        let contexts = [
            ArchiveCaptureContext(ordinaryCloudMessage: true),
            ArchiveCaptureContext(secretChat: true),
            ArchiveCaptureContext(ordinaryCloudMessage: true, expiring: true),
            ArchiveCaptureContext(secretChat: true, expiring: true, viewOnce: true)
        ]
        let expected = [1, 2, 2, 4]
        for (index, flags) in [(false, false), (true, false), (false, true), (true, true)].enumerated() {
            let captures = contexts.enumerated().map { self.capture(self.event("opt-in-\(index)-\($0.offset)"), $0.element) }
            let result: ArchiveWriteResult = try self.awaitResult {
                store.record(captures, generation: 0,
                    policy: ArchiveCapturePolicy(enabled: true, includeSecretChats: flags.0, includeExpiringMessages: flags.1), completion: $0)
            }
            XCTAssertEqual(result.inserted, expected[index])
            XCTAssertEqual(result.excluded, 4 - expected[index])
        }
    }

    func testExtendedPolicyNeverOverridesAccountPeerSenderOrBotExclusions() throws {
        let store = try self.open()
        let policy = ArchiveCapturePolicy(enabled: true, excludedPeers: [10], excludedSenders: [20], excludeBots: true,
            includeSecretChats: true, includeExpiringMessages: true)
        let context = ArchiveCaptureContext(secretChat: true, expiring: true, viewOnce: true)
        let captures = [self.capture(self.event("peer-excluded", peer: 10), context),
            self.capture(self.event("sender-excluded", author: 20), context),
            self.capture(self.event("sender-unknown", author: nil), context),
            self.capture(self.event("bot-excluded"), ArchiveCaptureContext(secretChat: true, expiring: true, botConversation: true))]
        let result: ArchiveWriteResult = try self.awaitResult { store.record(captures, generation: 0, policy: policy, completion: $0) }
        XCTAssertEqual(result.excluded, 4); XCTAssertEqual(result.inserted, 0)
        self.assertError(.accountMismatch) {
            try self.awaitResult { store.record([self.capture(self.event("wrong-account", account: 2), context)], generation: 0, policy: policy, completion: $0) }
        }
    }

    func testExplicitExtendedPolicyRejectsAmbiguousSourceAndMalformedViewOnceContext() throws {
        let store = try self.open()
        let policy = ArchiveCapturePolicy(enabled: true, includeSecretChats: true, includeExpiringMessages: true)
        let contexts = [ArchiveCaptureContext(), ArchiveCaptureContext(ordinaryCloudMessage: true, secretChat: true),
            ArchiveCaptureContext(ordinaryCloudMessage: true, viewOnce: true), ArchiveCaptureContext(secretChat: true, viewOnce: true)]
        let result: ArchiveWriteResult = try self.awaitResult {
            store.record(contexts.enumerated().map { self.capture(self.event("invalid-extended-\($0.offset)"), $0.element) },
                generation: 0, policy: policy, completion: $0)
        }
        XCTAssertEqual(result.excluded, 4)
        XCTAssertTrue(try self.records(store).isEmpty)
    }

    func testSecretZeroMessageIdentitySurvivesEncryptedStoreReopen() throws {
        let store = try self.open()
        let zero = ArchiveEvent(eventKey: "secret-zero-id",
            message: ArchiveMessageID(accountId: 1, peerId: 42, namespace: 2, messageId: 0), kind: .deleted,
            observedAt: 900, content: ArchiveContent(text: "secret zero fixture", sentAt: 800))
        let result: ArchiveWriteResult = try self.awaitResult {
            store.record([self.capture(zero, ArchiveCaptureContext(secretChat: true))], generation: 0,
                policy: ArchiveCapturePolicy(enabled: true, includeSecretChats: true), completion: $0)
        }
        XCTAssertEqual(result.inserted, 1)
        self.close(store)
        let reopened = try self.open()
        let timeline: [ArchivedRecord] = try self.awaitResult { reopened.timeline(for: zero.message, completion: $0) }
        XCTAssertEqual(timeline.map(\.event), [zero])
        self.assertError(.invalidInput) { try self.write(reopened, [self.event("cloud-zero-is-invalid", message: 0)]) }
        self.assertError(.invalidInput) { try self.write(reopened, [self.event("negative-is-invalid", message: -1)]) }
    }

}
