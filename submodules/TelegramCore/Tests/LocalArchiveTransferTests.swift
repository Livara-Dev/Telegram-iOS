// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

/// These tests use the production encrypted Postbox, coordinator and SQLCipher
/// store. Only Keychain is substituted with isolated synthetic test keys.
final class LocalArchiveTransferTests: XCTestCase {
    private final class Keys: ArchiveKeyProvider {
        let requests = Atomic<[Bool]>(value: [])
        let bytes: Data
        init(_ byte: UInt8) { self.bytes = Data(repeating: byte, count: 32) }
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
            let _ = self.requests.modify { $0 + [createIfMissing] }
            return self.bytes
        }
    }

    private final class Running {
        var coordinator: LocalArchiveCoordinator?
        var lease: Disposable?
        let keys = Keys(0x63)
        let probed = ValuePromise<Bool>(false, ignoreRepeated: true)
        let stores = Atomic<[ArchiveStore]>(value: [])
    }

    private let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(51))
    private let password = "a separate archive backup password"
    private let media = Data("an archived media payload".utf8)
    private var fixture: LocalArchivePostboxFixture!
    private var root: URL!
    private var running: [Running] = []
    private var sourceStores: [ArchiveStore] = []

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-transfer-facade")
        self.root = FileManager.default.temporaryDirectory.appendingPathComponent("archive-transfer-facade-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: self.root, withIntermediateDirectories: true)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(self.owner)], update: { _, peer in peer })
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: false))
        })
    }

    override func tearDownWithError() throws {
        for instance in self.running { try self.stop(instance) }
        for store in self.sourceStores { try self.close(store) }
        self.running.removeAll(); self.sourceStores.removeAll()
        try self.fixture?.destroy(); self.fixture = nil
        if let root = self.root { try? FileManager.default.removeItem(at: root) }
    }

    private func awaitResult<T, E: Error>(_ action: (@escaping (Result<T, E>) -> Void) -> Void) throws -> T {
        let finished = self.expectation(description: "real archive transfer result")
        let result = Atomic<Result<T, E>?>(value: nil)
        action { value in let _ = result.swap(value); finished.fulfill() }
        self.wait(for: [finished], timeout: 60)
        return try XCTUnwrap(result.with { $0 }).get()
    }

    private func transferValue(_ signal: Signal<LocalArchiveTransferResult, NoError>) throws -> LocalArchiveTransferResult {
        let finished = self.expectation(description: "real account transfer signal")
        let result = Atomic<LocalArchiveTransferResult?>(value: nil)
        let disposable = (signal |> take(1)).start(next: { value in
            let _ = result.swap(value)
        }, completed: { finished.fulfill() })
        defer { disposable.dispose() }
        self.wait(for: [finished], timeout: 60)
        return try XCTUnwrap(result.with { $0 })
    }

    private func close(_ store: ArchiveStore) throws {
        let finished = self.expectation(description: "close transfer store")
        store.close { finished.fulfill() }
        self.wait(for: [finished], timeout: 15)
    }

    private func start(owner: PeerId? = nil, testingEnvironment: Bool = false,
                       protectedData: Signal<Bool, NoError> = .single(true)) -> Running {
        let instance = Running()
        instance.coordinator = LocalArchiveCoordinator(postbox: self.fixture.postbox,
            accountPeerId: owner ?? self.owner, accountId: 72, testingEnvironment: testingEnvironment,
            baseDirectory: self.root.appendingPathComponent("destination"),
            storageFactory: { directory, accountId, retention, createIfMissing, callbackQueue, completion in
                // A missing existing-only probe must not request a key.
                instance.probed.set(true)
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: instance.keys,
                    createIfMissing: createIfMissing, retention: retention, callbackQueue: callbackQueue,
                    clock: { 1000 }) { result in
                    if case let .success(store) = result { let _ = instance.stores.modify { $0 + [store] } }
                    completion(result.map { $0 as LocalArchiveStorage })
                }
            }, protectedData: protectedData)
        instance.lease = instance.coordinator?.acquireReadAccess()
        self.running.append(instance)
        return instance
    }

    private func awaitMissing(_ instance: Running) throws -> LocalArchiveCoordinator {
        let coordinator = try XCTUnwrap(instance.coordinator)
        let _ = try self.localArchiveValue(instance.probed.get() |> filter { $0 })
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == .disabled }), .disabled)
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty)
        return coordinator
    }

    private func stop(_ instance: Running) throws {
        instance.lease?.dispose(); instance.lease = nil
        instance.coordinator?.dispose(); instance.coordinator = nil
        for store in instance.stores.with({ $0 }) { try self.close(store) }
    }

    private func backup(ownerId: Int64? = nil, testingEnvironment: Bool = false) throws -> URL {
        let source: ArchiveStore = try self.awaitResult {
            ArchiveStore.open(baseDirectory: self.root.appendingPathComponent("source"), accountId: 17,
                keyProvider: Keys(0x31), clock: { 1000 }, completion: $0)
        }
        self.sourceStores.append(source)
        let event = ArchiveEvent(eventKey: "same-telegram-user-event",
            message: .init(accountId: 17, peerId: 100, namespace: 0, messageId: 1),
            kind: .deleted, observedAt: 900,
            content: .init(text: "restored through the real account facade", authorId: 100, sentAt: 800))
        let _: ArchiveWriteResult = try self.awaitResult {
            source.record([ArchiveCapture(event: event, context: .init(ordinaryCloudMessage: true))],
                generation: 0, policy: .init(enabled: true), completion: $0)
        }
        let _: Bool = try self.awaitResult {
            source.recordMedia(eventKey: event.eventKey, slot: 0, kind: "document", mimeType: "application/octet-stream",
                data: self.media, generation: 0, completion: $0)
        }
        let url = self.root.appendingPathComponent(UUID().uuidString + ".ayuarchive")
        let _: ArchiveTransferExport = try self.awaitResult {
            source.exportBackup(to: url, password: self.password, ownerId: ownerId ?? self.owner.toInt64(),
                testingEnvironment: testingEnvironment, cancellation: ArchiveTransferCancellation(), completion: $0)
        }
        return url
    }

    private func assertFailure(_ result: LocalArchiveTransferResult, _ expected: LocalArchiveTransferFailure,
                               file: StaticString = #filePath, line: UInt = #line) {
        guard case let .failed(actual) = result else { return XCTFail("unexpected transfer result", file: file, line: line) }
        XCTAssertEqual(actual, expected, file: file, line: line)
    }

    func testWrongPasswordAndMalformedFileNeverCreateDestinationOrRequestKey() throws {
        let url = try self.backup()
        let instance = self.start(), coordinator = try self.awaitMissing(instance)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: "another valid backup password")), .authenticationFailed)
        let malformed = self.root.appendingPathComponent("malformed.ayuarchive")
        try Data("not an encrypted archive".utf8).write(to: malformed)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: malformed, password: self.password)), .invalidArchive)
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.root.appendingPathComponent("destination/account-72/archive.sqlite").path))
    }

    func testOwnerAndEnvironmentAreAuthenticatedBeforePersistentDestinationCreation() throws {
        let wrongOwner = try self.backup(ownerId: self.owner.toInt64() + 1)
        let wrongEnvironment = try self.backup(testingEnvironment: true)
        let instance = self.start(), coordinator = try self.awaitMissing(instance)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: wrongOwner, password: self.password)), .wrongAccount)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: wrongEnvironment, password: self.password)), .wrongAccount)
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty)
    }

    func testExplicitImportCreatesNewAccountArchiveThenReadsExportsAndReopensWithoutEnablingCapture() throws {
        let url = try self.backup()
        let instance = self.start(), coordinator = try self.awaitMissing(instance)
        guard case let .imported(added, duplicates, media, removed) = try self.transferValue(coordinator.importBackup(from: url, password: self.password)) else {
            return XCTFail("authenticated backup did not merge")
        }
        XCTAssertEqual(added, 1); XCTAssertEqual(duplicates, 0); XCTAssertEqual(media, 1); XCTAssertEqual(removed, 0)
        XCTAssertEqual(instance.keys.requests.with { $0 }, [true])
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == .browsing }), .browsing)
        XCTAssertFalse(try self.localArchiveValue(coordinator.settings).enabled)
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertEqual(page.records.map { $0.text }, ["restored through the real account facade"])
        XCTAssertEqual(try self.localArchiveValue(coordinator.mediaRange(eventKey: "same-telegram-user-event", slot: 0, offset: 0, count: self.media.count)), self.media)
        guard case let .exported(file) = try self.transferValue(coordinator.exportBackup(password: self.password)) else {
            return XCTFail("real account facade did not export")
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.url.path))
        XCTAssertGreaterThan(file.byteCount, 4096)
        let targetKey = instance.keys.bytes
        XCTAssertNil(try Data(contentsOf: file.url).range(of: targetKey))
        try self.stop(instance)
        try self.fixture.reopen()
        let reopened = self.start(), newCoordinator = try XCTUnwrap(reopened.coordinator)
        XCTAssertEqual(try self.localArchiveValue(newCoordinator.status |> filter { $0 == .browsing }), .browsing)
        XCTAssertEqual(reopened.keys.requests.with { $0 }, [false])
        XCTAssertEqual(try self.localArchiveValue(newCoordinator.deletedMessages()).records.map { $0.eventKey }, ["same-telegram-user-event"])
        XCTAssertEqual(try self.localArchiveValue(newCoordinator.mediaRange(eventKey: "same-telegram-user-event", slot: 0, offset: 0, count: self.media.count)), self.media)
        XCTAssertFalse(try self.localArchiveValue(newCoordinator.settings).enabled)
    }

    func testCaptureMustBePausedBeforeImportAndNoReadLeaseCannotExport() throws {
        let url = try self.backup()
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
        let instance = self.start(), coordinator = try XCTUnwrap(instance.coordinator)
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == .ready }), .ready)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: self.password)), .captureMustBePaused)
        XCTAssertEqual(try self.localArchiveValue(coordinator.deletedMessages()).records.count, 0)
        instance.lease?.dispose(); instance.lease = nil
        self.assertFailure(try self.transferValue(coordinator.exportBackup(password: self.password)), .unavailable)
    }

    func testLockedArchiveRefusesImportWithoutDestinationKeyAccess() throws {
        let url = try self.backup()
        let instance = self.start(protectedData: .single(false)), coordinator = try XCTUnwrap(instance.coordinator)
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == .locked }), .locked)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: self.password)), .unavailable)
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty)
    }
}
