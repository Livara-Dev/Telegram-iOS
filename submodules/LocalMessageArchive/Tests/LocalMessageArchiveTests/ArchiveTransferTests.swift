// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Darwin
import XCTest
import sqlcipher
@testable import LocalMessageArchive

final class ArchiveTransferTests: XCTestCase {
    private final class Keys: ArchiveKeyProvider {
        let bytes: Data
        init(_ byte: UInt8) { self.bytes = Data(repeating: byte, count: 32) }
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data { self.bytes }
    }
    private final class Once {
        private let lock = NSLock()
        private var used = false
        func claim() -> Bool {
            self.lock.lock(); defer { self.lock.unlock() }
            if self.used { return false }
            self.used = true; return true
        }
    }
    private final class MutationLog {
        private let lock = NSLock()
        private var value = 0
        var count: Int { self.lock.lock(); defer { self.lock.unlock() }; return self.value }
        func record() { self.lock.lock(); self.value += 1; self.lock.unlock() }
    }
    private var root: URL!
    private var stores: [ArchiveStore] = []
    private let password = "separate local backup passphrase"
    private let owner: Int64 = 400

    override func setUpWithError() throws {
        self.root = FileManager.default.temporaryDirectory.appendingPathComponent("archive-transfer-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: self.root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        for store in self.stores {
            let done = self.expectation(description: "close archive")
            store.close { done.fulfill() }; self.wait(for: [done], timeout: 10)
        }
        try? FileManager.default.removeItem(at: self.root)
    }

    private func awaitResult<T, E: Error>(_ action: (@escaping (Result<T, E>) -> Void) -> Void) throws -> T {
        let done = self.expectation(description: "archive transfer")
        var result: Result<T, E>?
        action { result = $0; done.fulfill() }
        self.wait(for: [done], timeout: 60)
        return try XCTUnwrap(result).get()
    }

    private func open(_ recordId: Int64, key: UInt8 = 0x35,
                      retention: ArchiveRetentionPolicy = .init()) throws -> ArchiveStore {
        let store: ArchiveStore = try self.awaitResult {
            ArchiveStore.open(baseDirectory: self.root, accountId: recordId, keyProvider: Keys(key),
                              retention: retention, clock: { 1000 }, completion: $0)
        }
        self.stores.append(store); return store
    }

    private func event(_ key: String, account: Int64, text: String = "private text canary", message: Int32 = 10,
                       kind: ArchiveEventKind = .deleted, observed: Int64 = 900) -> ArchiveEvent {
        return ArchiveEvent(eventKey: key,
            message: ArchiveMessageID(accountId: account, peerId: 42, namespace: 0, messageId: message),
            kind: kind, observedAt: observed, serverEditAt: kind == .edited ? 850 : nil,
            content: ArchiveContent(text: text, authorId: 9, sentAt: 800, entities: Data("private entity canary".utf8)))
    }

    private func write(_ store: ArchiveStore, _ events: [ArchiveEvent], generation: Int64 = 0) throws {
        let _: ArchiveWriteResult = try self.awaitResult {
            store.record(events.map { ArchiveCapture(event: $0, context: .init(ordinaryCloudMessage: true)) },
                         generation: generation, policy: .init(enabled: true), completion: $0)
        }
    }

    private func records(_ store: ArchiveStore) throws -> [ArchivedRecord] {
        return try self.awaitResult { store.deletedMessages(completion: $0) }
    }

    private func observeMutations(_ store: ArchiveStore) -> MutationLog {
        let log = MutationLog()
        let installed = self.expectation(description: "transfer mutation observer installed")
        store.setMutationObserver({ log.record() }, completion: { installed.fulfill() })
        self.wait(for: [installed], timeout: 10)
        return log
    }

    @discardableResult
    private func export(_ store: ArchiveStore, name: String = "backup.ayuarchive", owner: Int64? = nil) throws -> URL {
        let url = self.root.appendingPathComponent(name)
        let report: ArchiveTransferExport = try self.awaitResult {
            store.exportBackup(to: url, password: self.password, ownerId: owner ?? self.owner,
                               cancellation: ArchiveTransferCancellation(), completion: $0)
        }
        XCTAssertEqual(report.byteCount, Int64(try Data(contentsOf: url).count))
        return url
    }

    @discardableResult
    private func restore(_ store: ArchiveStore, url: URL, password: String? = nil,
                         owner: Int64? = nil, generation: Int64 = 0) throws -> ArchiveTransferImport {
        return try self.awaitResult {
            store.importBackup(from: url, password: password ?? self.password, ownerId: owner ?? self.owner,
                               expectedGeneration: generation, cancellation: ArchiveTransferCancellation(), completion: $0)
        }
    }

    func testPasswordBackupRoundTripsVersionsAndAllOrdinaryMediaAcrossLocalAccountRecords() throws {
        let source = try self.open(1)
        try self.write(source, [self.event("original", account: 1, text: "before edit", kind: .edited),
                               self.event("deleted", account: 1, text: "after edit")])
        let kinds = ["photo", "video", "animation", "voice", "document"]
        for (slot, kind) in kinds.enumerated() {
            let _: Bool = try self.awaitResult {
                source.recordMedia(eventKey: "deleted", slot: slot, kind: kind, mimeType: "application/octet-stream",
                    data: Data(("private media canary " + kind).utf8), generation: 0, completion: $0)
            }
        }
        let url = try self.export(source)
        let exported = try Data(contentsOf: url)
        for canary in [Data("before edit".utf8), Data("after edit".utf8), Data("private entity canary".utf8),
                       Data("private media canary".utf8), Data(repeating: 0x35, count: 32)] {
            XCTAssertNil(exported.range(of: canary))
        }
        let destination = try self.open(2, key: 0x71)
        let report = try self.restore(destination, url: url)
        XCTAssertEqual(report.addedVersions, 2); XCTAssertEqual(report.addedMedia, 5)
        let timeline: [ArchivedRecord] = try self.awaitResult {
            destination.timeline(for: .init(accountId: 2, peerId: 42, namespace: 0, messageId: 10), completion: $0)
        }
        XCTAssertEqual(timeline.map { $0.event.content.text }, ["before edit", "after edit"])
        XCTAssertTrue(timeline.allSatisfy { $0.event.message.accountId == 2 })
        for (slot, kind) in kinds.enumerated() {
            let media: ArchivedMedia? = try self.awaitResult { destination.media(eventKey: "deleted", slot: slot, completion: $0) }
            XCTAssertEqual(media?.data, Data(("private media canary " + kind).utf8))
        }
        let done = self.expectation(description: "close before reopen")
        destination.close { done.fulfill() }; self.wait(for: [done], timeout: 10)
        let reopened = try self.open(2, key: 0x71)
        XCTAssertEqual(try self.records(reopened).map { $0.event.eventKey }, ["deleted"])
        let media: ArchivedMedia? = try self.awaitResult { reopened.media(eventKey: "deleted", slot: 4, completion: $0) }
        XCTAssertEqual(media?.data, Data("private media canary document".utf8))
    }

    func testRepeatedImportDeduplicatesVersionsAndMediaWithoutChangingKeys() throws {
        let source = try self.open(1); try self.write(source, [self.event("one", account: 1)])
        let _: Bool = try self.awaitResult { source.recordMedia(eventKey: "one", slot: 0, kind: "photo", mimeType: "image/jpeg", data: Data([1, 2, 3]), generation: 0, completion: $0) }
        let url = try self.export(source), destination = try self.open(2, key: 0x72)
        let mutations = self.observeMutations(destination)
        _ = try self.restore(destination, url: url)
        XCTAssertEqual(mutations.count, 1, "transfer COMMIT invalidates before its completion on the serial callback queue")
        let again = try self.restore(destination, url: url)
        XCTAssertEqual(again.addedVersions, 0); XCTAssertEqual(again.duplicateVersions, 1); XCTAssertEqual(again.addedMedia, 0)
        XCTAssertEqual(try self.records(destination).count, 1)
        XCTAssertEqual(mutations.count, 1, "a no-op repeated import does not invalidate its own readers")
    }

    func testWrongPasswordAndWrongTelegramOwnerNeverChangeExistingArchive() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let url = try self.export(source), destination = try self.open(2, key: 0x73)
        try self.write(destination, [self.event("keep", account: 2)])
        XCTAssertThrowsError(try self.restore(destination, url: url, password: "different valid passphrase")) {
            XCTAssertEqual($0 as? ArchiveTransferError, .authenticationFailed)
        }
        XCTAssertThrowsError(try self.restore(destination, url: url, owner: self.owner + 1)) {
            XCTAssertEqual($0 as? ArchiveTransferError, .wrongAccount)
        }
        XCTAssertEqual(try self.records(destination).map { $0.event.eventKey }, ["keep"])
    }

    func testTruncatedAndTamperedPayloadsNeverPartiallyImport() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let url = try self.export(source), original = try Data(contentsOf: url), destination = try self.open(2)
        let truncated = self.root.appendingPathComponent("truncated.ayuarchive")
        try original.dropLast(1).write(to: truncated)
        XCTAssertThrowsError(try self.restore(destination, url: truncated))
        var changed = original; changed[changed.count - 100] ^= 0x40
        let tampered = self.root.appendingPathComponent("tampered.ayuarchive"); try changed.write(to: tampered)
        XCTAssertThrowsError(try self.restore(destination, url: tampered)) {
            XCTAssertEqual($0 as? ArchiveTransferError, .authenticationFailed)
        }
        XCTAssertTrue(try self.records(destination).isEmpty)
    }

    func testVersionConflictRollsBackEarlierInsertedVersions() throws {
        let source = try self.open(1)
        try self.write(source, [self.event("new-first", account: 1), self.event("conflict", account: 1, text: "source version")])
        let url = try self.export(source), destination = try self.open(2)
        try self.write(destination, [self.event("conflict", account: 2, text: "destination version")])
        let mutations = self.observeMutations(destination)
        XCTAssertThrowsError(try self.restore(destination, url: url)) { XCTAssertEqual($0 as? ArchiveTransferError, .conflict) }
        XCTAssertEqual(try self.records(destination).map { $0.event.content.text }, ["destination version"])
        XCTAssertEqual(mutations.count, 0, "transfer rollback is not a committed mutation")
        XCTAssertEqual(try self.awaitResult { destination.setRetention(ArchiveRetentionPolicy(), completion: $0) }, 0)
        XCTAssertEqual(mutations.count, 0, "rolled-back total_changes must not contaminate a later no-op transaction")
    }

    func testConflictingMediaRollsBackWholeImportWithBoundedBlobReads() throws {
        let source = try self.open(1); try self.write(source, [self.event("same", account: 1), self.event("new", account: 1)])
        let bytes = Data(repeating: 0x52, count: 2 * 1024 * 1024)
        let _: Bool = try self.awaitResult { source.recordMedia(eventKey: "same", slot: 0, kind: "document", mimeType: "application/octet-stream", data: bytes, generation: 0, completion: $0) }
        let url = try self.export(source), destination = try self.open(2)
        try self.write(destination, [self.event("same", account: 2)])
        let _: Bool = try self.awaitResult { destination.recordMedia(eventKey: "same", slot: 0, kind: "document", mimeType: "application/octet-stream", data: Data(repeating: 0x53, count: bytes.count), generation: 0, completion: $0) }
        XCTAssertThrowsError(try self.restore(destination, url: url)) { XCTAssertEqual($0 as? ArchiveTransferError, .conflict) }
        XCTAssertEqual(try self.records(destination).map { $0.event.eventKey }, ["same"])
    }

    func testRestorePreservesDestinationGenerationAndReplayReceipt() throws {
        let source = try self.open(1)
        let _: ArchivePurgeResult = try self.awaitResult { source.purge(advancingToGeneration: 1, completion: $0) }
        try self.write(source, [self.event("restored", account: 1)], generation: 1)
        let url = try self.export(source), destination = try self.open(2)
        let _: ArchiveWriteResult = try self.awaitResult {
            destination.recordFromOutbox(.init(event: self.event("receipt", account: 2), context: .init(ordinaryCloudMessage: true)), index: 50, generation: 0, policy: .init(enabled: true), completion: $0)
        }
        _ = try self.restore(destination, url: url)
        let generation: Int64 = try self.awaitResult { destination.currentGeneration(completion: $0) }
        XCTAssertEqual(generation, 0)
        let replay: ArchiveWriteResult = try self.awaitResult {
            destination.recordFromOutbox(.init(event: self.event("old-replay", account: 2), context: .init(ordinaryCloudMessage: true)), index: 49, generation: 0, policy: .init(enabled: true), completion: $0)
        }
        XCTAssertEqual(replay.inserted, 0)
        XCTAssertFalse(try self.records(destination).contains { $0.event.eventKey == "old-replay" })
    }

    func testCurrentRetentionLimitsApplyToRestoredHistory() throws {
        let source = try self.open(1)
        try self.write(source, [self.event("too-old", account: 1, observed: 800), self.event("recent", account: 1, observed: 990)])
        let url = try self.export(source)
        let destination = try self.open(2, retention: .init(maxAgeSeconds: 50, maxPayloadBytes: 1024 * 1024))
        let restored = try self.restore(destination, url: url)
        XCTAssertEqual(restored.removedByRetention, 1)
        XCTAssertEqual(try self.records(destination).map { $0.event.eventKey }, ["recent"])
    }

    func testCancellationBeforeTransferDoesNotWriteAFileOrMerge() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let cancellation = ArchiveTransferCancellation(); cancellation.cancel()
        let rejected = self.root.appendingPathComponent("cancelled.ayuarchive")
        XCTAssertThrowsError(try self.awaitResult { completion in
            source.exportBackup(to: rejected, password: self.password, ownerId: self.owner, cancellation: cancellation, completion: completion)
        } as ArchiveTransferExport) { XCTAssertEqual($0 as? ArchiveTransferError, .cancelled) }
        XCTAssertFalse(FileManager.default.fileExists(atPath: rejected.path))
        let url = try self.export(source), destination = try self.open(2)
        XCTAssertThrowsError(try self.awaitResult { completion in
            destination.importBackup(from: url, password: self.password, ownerId: self.owner, expectedGeneration: 0, cancellation: cancellation, completion: completion)
        } as ArchiveTransferImport) { XCTAssertEqual($0 as? ArchiveTransferError, .cancelled) }
        XCTAssertTrue(try self.records(destination).isEmpty)
    }

    func testExistingDestinationAndSymlinkInputsAreNeverOverwrittenOrFollowed() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let sentinel = self.root.appendingPathComponent("keep.ayuarchive"), contents = Data("existing owner file".utf8)
        try contents.write(to: sentinel)
        XCTAssertThrowsError(try self.export(source, name: sentinel.lastPathComponent))
        XCTAssertEqual(try Data(contentsOf: sentinel), contents)
        let url = try self.export(source), link = self.root.appendingPathComponent("link.ayuarchive")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: url)
        let destination = try self.open(2)
        XCTAssertThrowsError(try self.restore(destination, url: link))
        XCTAssertTrue(try self.records(destination).isEmpty)
    }

    func testPreparedImportChecksOwnershipBeforeAnyDestinationStoreIsCreated() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let url = try self.export(source)
        XCTAssertThrowsError(try self.awaitResult { completion in
            ArchiveStore.prepareImport(from: url, password: self.password, ownerId: self.owner + 1,
                                       cancellation: ArchiveTransferCancellation(), completion: completion)
        } as ArchivePreparedImport) { XCTAssertEqual($0 as? ArchiveTransferError, .wrongAccount) }
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.root.appendingPathComponent("account-2").path))
    }

    func testInvalidShortPasswordAndStaleGenerationFailWithoutMerging() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let url = try self.export(source), destination = try self.open(2)
        XCTAssertThrowsError(try self.restore(destination, url: url, password: "short")) {
            XCTAssertEqual($0 as? ArchiveTransferError, .invalidPassword)
        }
        XCTAssertThrowsError(try self.restore(destination, url: url, generation: 1)) {
            XCTAssertEqual($0 as? ArchiveTransferError, .unavailable)
        }
        XCTAssertTrue(try self.records(destination).isEmpty)
    }

    func testEmptyEntityBlobsRemainBlobsAfterRestore() throws {
        let source = try self.open(1)
        let event = ArchiveEvent(eventKey: "empty-entities", message: .init(accountId: 1, peerId: 42, namespace: 0, messageId: 10),
                                 kind: .deleted, observedAt: 900, content: .init(text: "plain text", sentAt: 800))
        try self.write(source, [event])
        let url = try self.export(source), destination = try self.open(2)
        _ = try self.restore(destination, url: url)
        XCTAssertEqual(try self.records(destination).first?.event.content.entities, Data())
    }

    func testUnknownFormatAndExcessivePasswordWorkAreRejected() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let url = try self.export(source), original = try Data(contentsOf: url), destination = try self.open(2)
        let length = original[8..<12].reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        let range = 12..<(12 + Int(length))
        let old = try JSONDecoder().decode(ArchiveTransferEnvelope.Header.self, from: Data(original[range]))
        for version in [UInt32(1), 2] {
            let header = ArchiveTransferEnvelope.Header(version: version, iterations: version == 1 ? UInt32.max : old.iterations,
                salt: old.salt, payloadBytes: old.payloadBytes, payloadDigest: old.payloadDigest, wrappedKey: old.wrappedKey)
            let encoded = try JSONEncoder().encode(header)
            var size = UInt32(encoded.count).bigEndian
            var changed = ArchiveTransferEnvelope.magic
            Swift.withUnsafeBytes(of: &size) { changed.append(contentsOf: $0) }
            changed.append(encoded); changed.append(original.suffix(from: range.upperBound))
            let target = self.root.appendingPathComponent("header-\(version).ayuarchive"); try changed.write(to: target)
            XCTAssertThrowsError(try self.restore(destination, url: target)) {
                XCTAssertEqual($0 as? ArchiveTransferError, version == 2 ? .unsupportedVersion : .invalidArchive)
            }
        }
        XCTAssertTrue(try self.records(destination).isEmpty)
    }

    func testEqualUserIdsRemainSeparatedBetweenTestAndProductionEnvironments() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let url = self.root.appendingPathComponent("test-environment.ayuarchive")
        let _: ArchiveTransferExport = try self.awaitResult {
            source.exportBackup(to: url, password: self.password, ownerId: self.owner, testingEnvironment: true,
                                cancellation: ArchiveTransferCancellation(), completion: $0)
        }
        let destination = try self.open(2)
        XCTAssertThrowsError(try self.restore(destination, url: url)) { XCTAssertEqual($0 as? ArchiveTransferError, .wrongAccount) }
        let report: ArchiveTransferImport = try self.awaitResult {
            destination.importBackup(from: url, password: self.password, ownerId: self.owner, testingEnvironment: true,
                                     expectedGeneration: 0, cancellation: ArchiveTransferCancellation(), completion: $0)
        }
        XCTAssertEqual(report.addedVersions, 1)
    }

    func testRealTemporaryDirectoryAndGlobalPlaintextHeaderDefaultRemainEncrypted() throws {
        var connection: OpaquePointer?
        XCTAssertEqual(sqlite3_open(":memory:", &connection), SQLITE_OK)
        let database = try XCTUnwrap(connection)
        var oldQuery: OpaquePointer?
        XCTAssertEqual(sqlite3_prepare_v2(database, "PRAGMA cipher_default_plaintext_header_size", -1, &oldQuery, nil), SQLITE_OK)
        let oldStatement = try XCTUnwrap(oldQuery)
        XCTAssertEqual(sqlite3_step(oldStatement), SQLITE_ROW)
        let previous = sqlite3_column_int(oldStatement, 0)
        sqlite3_finalize(oldStatement)
        defer {
            sqlite3_exec(database, "PRAGMA cipher_default_plaintext_header_size=\(previous)", nil, nil, nil)
            sqlite3_close_v2(database)
        }
        XCTAssertEqual(sqlite3_exec(database, "PRAGMA cipher_default_plaintext_header_size=32", nil, nil, nil), SQLITE_OK)
        let scratch = try ArchiveTransferFiles.scratchDirectory()
        defer { try? FileManager.default.removeItem(at: scratch) }
        XCTAssertEqual(scratch.path, try ArchiveTransferFiles.physicalURL(scratch).path)
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let url = try self.export(source)
        let token = ArchiveTransferCancellation()
        let decoded = scratch.appendingPathComponent("decoded.sqlite")
        let unlocked = try ArchiveTransferEnvelope.unseal(source: url, snapshot: decoded, ownerId: self.owner,
            testingEnvironment: false, password: self.password, cancellation: token)
        XCTAssertNotEqual(try Data(contentsOf: decoded).prefix(16), Data("SQLite format 3\0".utf8))
        let input = try ArchiveTransferDatabase.openSnapshot(decoded, key: unlocked.key, cancellation: token)
        sqlite3_close_v2(input)
        let destination = try self.open(2)
        XCTAssertEqual(try self.restore(destination, url: url).addedVersions, 1)
    }

    func testKnownSystemParentAliasesRequirePosixCanonicalTargets() throws {
        for (alias, expected) in [("/var", "/private/var"), ("/tmp", "/private/tmp")] {
            let url = URL(fileURLWithPath: alias, isDirectory: true)
            var info = stat()
            if lstat(alias, &info) != 0 {
                XCTAssertThrowsError(try ArchiveTransferFiles.validateParents(url))
                continue
            }
            if (info.st_mode & S_IFMT) == S_IFLNK {
                // The exact allowlisted target is a POSIX fact; Foundation's
                // preferred spelling is not evidence about a symlink target.
                let physical = try ArchiveTransferFiles.physicalURL(url)
                if physical.path == expected {
                    XCTAssertNoThrow(try ArchiveTransferFiles.validateParents(url))
                } else {
                    XCTAssertThrowsError(try ArchiveTransferFiles.validateParents(url))
                }
            } else {
                XCTAssertEqual(info.st_mode & S_IFMT, S_IFDIR)
                XCTAssertNoThrow(try ArchiveTransferFiles.validateParents(url))
            }
        }
    }

    func testCallerCreatedParentAliasCannotRedirectTransferFileOrDirectoryCreation() throws {
        let actual = self.root.appendingPathComponent("actual-parent", isDirectory: true)
        try FileManager.default.createDirectory(at: actual, withIntermediateDirectories: false)
        let link = self.root.appendingPathComponent("parent-alias", isDirectory: true)
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: actual)
        let original = actual.appendingPathComponent("source.ayuarchive")
        let canary = Data("synthetic transfer path canary".utf8)
        try canary.write(to: original)
        for operation in [
            { let fd = try ArchiveTransferFiles.create(link.appendingPathComponent("blocked.ayuarchive")); _ = Darwin.close(fd) },
            { try ArchiveTransferFiles.protectedDirectory(link.appendingPathComponent("blocked-directory", isDirectory: true)) },
            { let file = try ArchiveTransferFiles.openRead(link.appendingPathComponent("source.ayuarchive")); _ = Darwin.close(file.fd) }
        ] {
            XCTAssertThrowsError(try operation()) { XCTAssertEqual($0 as? ArchiveTransferError, .storageUnavailable) }
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: actual.appendingPathComponent("blocked.ayuarchive").path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: actual.appendingPathComponent("blocked-directory").path))
        XCTAssertEqual(try Data(contentsOf: original), canary)
    }

    func testCancellationAfterRealBlobWriteRollsBackVersionsAndMedia() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let _: Bool = try self.awaitResult {
            source.recordMedia(eventKey: "source", slot: 0, kind: "document", mimeType: "application/octet-stream",
                               data: Data(repeating: 0x63, count: 2 * 1024 * 1024), generation: 0, completion: $0)
        }
        let url = try self.export(source), destination = try self.open(2)
        try self.write(destination, [self.event("keep", account: 2)])
        let mutations = self.observeMutations(destination)
        let token = ArchiveTransferCancellation()
        let prepared: ArchivePreparedImport = try self.awaitResult {
            ArchiveStore.prepareImport(from: url, password: self.password, ownerId: self.owner,
                                       cancellation: token, completion: $0)
        }
        token.observeCheckpoints { [weak token] checkpoint in
            if checkpoint == .mediaChunk { token?.cancel() }
        }
        XCTAssertThrowsError(try self.awaitResult { completion in
            destination.importPreparedBackup(prepared, ownerId: self.owner, expectedGeneration: 0,
                                             cancellation: token, completion: completion)
        } as ArchiveTransferImport) { XCTAssertEqual($0 as? ArchiveTransferError, .cancelled) }
        XCTAssertEqual(try self.records(destination).map { $0.event.eventKey }, ["keep"])
        let media: ArchivedMedia? = try self.awaitResult { destination.media(eventKey: "source", slot: 0, completion: $0) }
        XCTAssertNil(media)
        XCTAssertEqual(mutations.count, 0, "cancelling after a real blob write rolls back without notifying observers")
        let closed = self.expectation(description: "close after cancelled merge")
        destination.close { closed.fulfill() }; self.wait(for: [closed], timeout: 10)
        let reopened = try self.open(2)
        XCTAssertEqual(try self.records(reopened).map { $0.event.eventKey }, ["keep"])
        let reopenedMedia: ArchivedMedia? = try self.awaitResult { reopened.media(eventKey: "source", slot: 0, completion: $0) }
        XCTAssertNil(reopenedMedia)
    }

    func testCancellationDuringExportReconstructionRemovesOutputAndPreservesSource() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let bytes = Data(repeating: 0x71, count: 2 * 1024 * 1024)
        let _: Bool = try self.awaitResult {
            source.recordMedia(eventKey: "source", slot: 0, kind: "document", mimeType: "application/octet-stream",
                               data: bytes, generation: 0, completion: $0)
        }
        let token = ArchiveTransferCancellation()
        token.observeCheckpoints { [weak token] checkpoint in
            if checkpoint == .mediaChunk { token?.cancel() }
        }
        let url = self.root.appendingPathComponent("cancelled-reconstruction.ayuarchive")
        XCTAssertThrowsError(try self.awaitResult { completion in
            source.exportBackup(to: url, password: self.password, ownerId: self.owner,
                                cancellation: token, completion: completion)
        } as ArchiveTransferExport) { XCTAssertEqual($0 as? ArchiveTransferError, .cancelled) }
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        XCTAssertEqual(try self.records(source).map { $0.event.eventKey }, ["source"])
        let media: ArchivedMedia? = try self.awaitResult { source.media(eventKey: "source", slot: 0, completion: $0) }
        XCTAssertEqual(media?.data, bytes)
    }

    func testConcurrentWalWriterDoesNotMixExportSnapshot() throws {
        let source = try self.open(1); try self.write(source, [self.event("before", account: 1)])
        let writer = try self.open(1)
        let token = ArchiveTransferCancellation(), once = Once()
        token.observeCheckpoints { checkpoint in
            guard checkpoint == .snapshotRow, once.claim() else { return }
            let finished = DispatchSemaphore(value: 0)
            writer.record([.init(event: self.event("after", account: 1), context: .init(ordinaryCloudMessage: true))],
                          generation: 0, policy: .init(enabled: true)) { result in
                if case .failure = result { XCTFail("Concurrent WAL writer failed") }
                finished.signal()
            }
            XCTAssertEqual(finished.wait(timeout: .now() + 10), .success)
        }
        let url = self.root.appendingPathComponent("concurrent.ayuarchive")
        let _: ArchiveTransferExport = try self.awaitResult {
            source.exportBackup(to: url, password: self.password, ownerId: self.owner, cancellation: token, completion: $0)
        }
        XCTAssertEqual(Set(try self.records(source).map { $0.event.eventKey }), ["before", "after"])
        let destination = try self.open(2)
        _ = try self.restore(destination, url: url)
        XCTAssertEqual(try self.records(destination).map { $0.event.eventKey }, ["before"])
    }

    func testPurgedFtsTokensAreNotCopiedIntoExportSnapshot() throws {
        let source = try self.open(1)
        let canary = Data("purgedsecretcanarytoken".utf8)
        try self.write(source, [self.event("purged", account: 1, text: String(decoding: canary, as: UTF8.self))])
        let _: ArchivePurgeResult = try self.awaitResult { source.purge(advancingToGeneration: 1, completion: $0) }
        try self.write(source, [self.event("live", account: 1, text: "retained visible word")], generation: 1)
        let url = try self.export(source)
        let prepared: ArchivePreparedImport = try self.awaitResult {
            ArchiveStore.prepareImport(from: url, password: self.password, ownerId: self.owner,
                                       cancellation: ArchiveTransferCancellation(), completion: $0)
        }
        var row: OpaquePointer?
        XCTAssertEqual(sqlite3_prepare_v2(prepared.database, "SELECT block FROM events_search_data", -1, &row, nil), SQLITE_OK)
        let statement = try XCTUnwrap(row); defer { sqlite3_finalize(statement) }
        var code = sqlite3_step(statement)
        while code == SQLITE_ROW {
            let count = Int(sqlite3_column_bytes(statement, 0))
            if let bytes = sqlite3_column_blob(statement, 0) { XCTAssertNil(Data(bytes: bytes, count: count).range(of: canary)) }
            code = sqlite3_step(statement)
        }
        XCTAssertEqual(code, SQLITE_DONE)
        let destination = try self.open(2)
        _ = try self.restore(destination, url: url)
        XCTAssertEqual(try self.records(destination).map { $0.event.eventKey }, ["live"])
    }

    private func alteredAuthenticatedBackup(_ source: ArchiveStore, name: String,
                                             mutation: (OpaquePointer) throws -> Void) throws -> URL {
        let exported = try self.export(source, name: "base-" + name)
        let scratch = try ArchiveTransferFiles.scratchDirectory()
        defer { try? FileManager.default.removeItem(at: scratch) }
        let snapshot = scratch.appendingPathComponent("modified.sqlite"), token = ArchiveTransferCancellation()
        let unlocked = try ArchiveTransferEnvelope.unseal(source: exported, snapshot: snapshot, ownerId: self.owner,
            testingEnvironment: false, password: self.password, cancellation: token)
        var opened: OpaquePointer?
        XCTAssertEqual(sqlite3_open_v2(snapshot.path, &opened, SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX | SQLITE_OPEN_NOFOLLOW, nil), SQLITE_OK)
        let database = try XCTUnwrap(opened)
        do {
            let hex = unlocked.key.map { String(format: "%02x", $0) }.joined()
            XCTAssertEqual(sqlite3_exec(database, "PRAGMA key=\"x'\(hex)'\"", nil, nil, nil), SQLITE_OK)
            XCTAssertEqual(sqlite3_exec(database, "PRAGMA cipher_plaintext_header_size=0", nil, nil, nil), SQLITE_OK)
            try mutation(database)
            XCTAssertEqual(sqlite3_close_v2(database), SQLITE_OK)
        } catch { sqlite3_close_v2(database); throw error }
        let result = self.root.appendingPathComponent(name)
        _ = try ArchiveTransferEnvelope.seal(snapshot: snapshot, destination: result,
            accountId: unlocked.accountId, ownerId: self.owner, testingEnvironment: false,
            transportKey: unlocked.key, password: self.password, cancellation: token)
        return result
    }

    func testAuthenticatedUnexpectedSchemaIsRejectedBeforeDestinationInitialization() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let malformed = try self.alteredAuthenticatedBackup(source, name: "extra-table.ayuarchive") { database in
            // '_' is a LIKE wildcard: sqliteXsecret must not be treated as the
            // reserved sqlite_ internal-table prefix by the schema whitelist.
            XCTAssertEqual(sqlite3_exec(database, "CREATE TABLE sqliteXsecret(value TEXT)", nil, nil, nil), SQLITE_OK)
        }
        XCTAssertThrowsError(try self.awaitResult { completion in
            ArchiveStore.prepareImport(from: malformed, password: self.password, ownerId: self.owner,
                                       cancellation: ArchiveTransferCancellation(), completion: completion)
        } as ArchivePreparedImport) { XCTAssertEqual($0 as? ArchiveTransferError, .unsupportedVersion) }
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.root.appendingPathComponent("account-2").path))
    }

    func testAuthenticatedOversizedRowIsRejectedBeforeDestinationInitialization() throws {
        let source = try self.open(1); try self.write(source, [self.event("source", account: 1)])
        let malformed = try self.alteredAuthenticatedBackup(source, name: "oversized-row.ayuarchive") { database in
            let text = String(repeating: "x", count: 300 * 1024)
            var query: OpaquePointer?
            XCTAssertEqual(sqlite3_prepare_v2(database, "INSERT INTO events(event_key,account_id,peer_id,namespace,message_id,kind,observed_at,server_edit_at,text,author_id,sent_at,entities,payload_bytes) VALUES('too-large',1,42,0,11,1,900,NULL,?,9,800,zeroblob(0),?)", -1, &query, nil), SQLITE_OK)
            let statement = try XCTUnwrap(query); defer { sqlite3_finalize(statement) }
            let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
            XCTAssertEqual(sqlite3_bind_text(statement, 1, text, Int32(text.utf8.count), transient), SQLITE_OK)
            XCTAssertEqual(sqlite3_bind_int64(statement, 2, Int64(text.utf8.count + "too-large".utf8.count + 64)), SQLITE_OK)
            XCTAssertEqual(sqlite3_step(statement), SQLITE_DONE)
        }
        XCTAssertThrowsError(try self.awaitResult { completion in
            ArchiveStore.prepareImport(from: malformed, password: self.password, ownerId: self.owner,
                                       cancellation: ArchiveTransferCancellation(), completion: completion)
        } as ArchivePreparedImport) { XCTAssertEqual($0 as? ArchiveTransferError, .invalidArchive) }
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.root.appendingPathComponent("account-2").path))
    }
}
